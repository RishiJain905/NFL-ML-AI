"""PC03: the Big Data Bowl library's Kaggle client (`playcalling/bdb.py`: `kaggle_auth`,
`list_files`, `kaggle_names`, `week_files`, `download`), against `httpx.MockTransport`: no
network, ever.

Every call passes `auth=` explicitly, so the settings module (and with it any env file) is never
touched; the tests that look at `kaggle_auth` itself replace `nflengine.settings.get_env`. The
competition's files are made up here. Two ways to sign in:

- a Kaggle API token (KAGGLE_API_TOKEN): `auth == ("", token)`, a Bearer header from the first
  request on, no probe;
- a classic username + key (HTTP basic): one probe of the file list; a 401 switches to Bearer.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from nflengine.paths import DataPaths
from nflengine.playcalling import bdb

AUTH = ("user", "testkey")
TOKEN_AUTH = ("", "testtoken")
BASIC = "Basic " + base64.b64encode(b"user:testkey").decode()
BEARER = "Bearer testkey"
BEARER_TOKEN = "Bearer testtoken"
LIST_PATH = f"/api/v1/competitions/data/list/{bdb.COMPETITION}"
DL_PATH = f"/api/v1/competitions/data/download/{bdb.COMPETITION}/"
SECRETS = ("testkey", "testtoken")


class Recorder:
    """A MockTransport handler that records every request and answers by a table."""

    def __init__(self, answer):
        self.answer = answer
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    @property
    def raw_paths(self) -> list[str]:
        return [r.url.raw_path.decode().split("?")[0] for r in self.requests]


def page(files, token=None) -> httpx.Response:
    body = {"files": files}
    if token:
        body["nextPageToken"] = token
    return httpx.Response(200, json=body)


def no_secret(text: str) -> bool:
    return not any(s in text for s in SECRETS)


@pytest.fixture
def paths(tmp_path) -> DataPaths:
    p = DataPaths(tmp_path)
    for d in p.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    return p


# ---- the edition ---------------------------------------------------------------------------------


def test_the_edition_is_bdb_2026_analytics():
    assert bdb.EDITION == "2026" and bdb.COMPETITION == "nfl-big-data-bowl-2026-analytics"
    assert bdb.SEASON == 2023 and bdb.WEEKS == (1, 18)
    assert bdb.NEEDED == ("supplementary_data.csv",)
    assert bdb.INPUT.format(season=2023, week=1) == "input_2023_w01.csv"
    assert bdb.OUTPUT.format(season=2023, week=18) == "output_2023_w18.csv"
    assert "2026" in bdb.NOTE and "non-commercial" in bdb.NOTE and "not redistributed" in bdb.NOTE


def test_the_raw_folder_follows_the_t00_layout(paths):
    assert bdb.raw_dir(paths) == Path(paths.root) / "bdb" / "2026" / "raw"
    assert bdb.library_dir(paths) == Path(paths.root) / "playcalling" / "bdb_library"


def test_week_files_are_the_input_and_output_of_each_week():
    assert bdb.week_files([1, 12]) == [
        "input_2023_w01.csv",
        "output_2023_w01.csv",
        "input_2023_w12.csv",
        "output_2023_w12.csv",
    ]
    assert bdb.week_files([]) == []


# ---- auth ----------------------------------------------------------------------------------------


def env(**kw):
    base = {"kaggle_api_token": None, "kaggle_username": None, "kaggle_key": None}
    return SimpleNamespace(**{**base, **kw})


@pytest.mark.parametrize(
    ("kw", "want"),
    [
        # a token is preferred, with no username (a Bearer header)
        ({"kaggle_api_token": SecretStr(" tok123 ")}, ("", "tok123")),
        (
            {
                "kaggle_api_token": SecretStr("tok123"),
                "kaggle_username": "kaggler",
                "kaggle_key": SecretStr("abc123"),
            },
            ("", "tok123"),
        ),
        # else the classic pair, stripped
        (
            {"kaggle_username": " kaggler ", "kaggle_key": SecretStr(" abc123 ")},
            ("kaggler", "abc123"),
        ),
        (
            {
                "kaggle_api_token": SecretStr("  "),
                "kaggle_username": "k",
                "kaggle_key": SecretStr("a"),
            },
            ("k", "a"),
        ),
        # both halves of the pair are needed
        ({"kaggle_username": None, "kaggle_key": SecretStr("abc123")}, None),
        ({"kaggle_username": "", "kaggle_key": SecretStr("abc123")}, None),
        ({"kaggle_username": "kaggler", "kaggle_key": None}, None),
        ({"kaggle_username": "kaggler", "kaggle_key": SecretStr("   ")}, None),
        ({"kaggle_api_token": SecretStr("")}, None),
        ({}, None),
    ],
)
def test_kaggle_auth_reads_the_settings_module(monkeypatch, kw, want):
    import nflengine.settings as S

    monkeypatch.setattr(S, "get_env", lambda: env(**kw))
    assert bdb.kaggle_auth() == want


def test_without_credentials_nothing_is_requested(monkeypatch, paths):
    monkeypatch.setattr(bdb, "kaggle_auth", lambda: None)
    rec = Recorder(lambda r: page([]))
    with pytest.raises(bdb.KaggleError) as e:
        bdb.list_files(transport=rec.transport)
    assert str(e.value) == bdb.NO_CREDENTIALS
    assert "KAGGLE_API_TOKEN" in bdb.NO_CREDENTIALS and "KAGGLE_USERNAME" in bdb.NO_CREDENTIALS
    with pytest.raises(bdb.KaggleError, match="KAGGLE_API_TOKEN"):
        bdb.download(paths, ["supplementary_data.csv"], transport=rec.transport)
    assert rec.requests == []


# ---- list_files ----------------------------------------------------------------------------------


def listing_answer(pages):
    def answer(r):
        if r.url.params.get("pageSize") == "1":  # the credential probe (basic auth only)
            return page([{"name": "supplementary_data.csv"}])
        return pages[r.url.params.get("pageToken")]

    return answer


PAGES = {
    None: page(
        [
            {"name": "supplementary_data.csv", "totalBytes": 100},
            {"name": "train/input_2023_w01.csv", "totalBytes": 2000},
        ],
        token="t2",
    ),
    "t2": page([{"name": "train/output_2023_w01.csv", "totalBytes": 3_000_000_000}]),
}


def test_list_files_follows_the_pages_and_reads_the_sizes_with_basic_auth():
    rec = Recorder(listing_answer(PAGES))
    files = bdb.list_files(auth=AUTH, transport=rec.transport)
    assert files == [
        {"name": "supplementary_data.csv", "bytes": 100},
        {"name": "train/input_2023_w01.csv", "bytes": 2000},
        {"name": "train/output_2023_w01.csv", "bytes": 3_000_000_000},
    ]
    assert rec.paths == [LIST_PATH] * 3
    probe, first, second = rec.requests
    assert probe.url.params["pageSize"] == "1"
    assert first.url.params["pageSize"] == "200" and "pageToken" not in first.url.params
    assert second.url.params["pageToken"] == "t2"
    assert {r.headers["authorization"] for r in rec.requests} == {BASIC}


def test_an_api_token_is_a_bearer_header_from_the_first_request_with_no_probe():
    rec = Recorder(listing_answer(PAGES))
    files = bdb.list_files(auth=TOKEN_AUTH, transport=rec.transport)
    assert len(files) == 3 and len(rec.requests) == 2  # two pages, no probe
    assert all(
        "pageSize" in r.url.params and r.url.params["pageSize"] == "200" for r in rec.requests
    )
    assert {r.headers["authorization"] for r in rec.requests} == {BEARER_TOKEN}
    assert not any(r.headers["authorization"].startswith("Basic") for r in rec.requests)


def test_list_files_accepts_the_other_shapes_kaggle_answers_with():
    body = [
        {"ref": "a.csv", "size": 5},
        {"name": "b.csv"},
        {"nothing": 1},
        {"name": "c.csv", "totalBytes": None},
    ]
    rec = Recorder(lambda r: httpx.Response(200, json=body))
    assert bdb.list_files(auth=AUTH, transport=rec.transport) == [
        {"name": "a.csv", "bytes": 5},
        {"name": "b.csv", "bytes": 0},
        {"name": "c.csv", "bytes": 0},
    ]
    rec = Recorder(lambda r: httpx.Response(200, json={"files": None}))
    assert bdb.list_files(auth=AUTH, transport=rec.transport) == []


def test_list_files_stops_after_fifty_pages_whatever_kaggle_says():
    rec = Recorder(lambda r: page([{"name": "x.csv", "totalBytes": 1}], token="again"))
    assert len(bdb.list_files(auth=AUTH, transport=rec.transport)) == 50
    assert len(rec.requests) == 51  # the probe + 50 pages
    rec = Recorder(lambda r: page([{"name": "x.csv", "totalBytes": 1}], token="again"))
    bdb.list_files(auth=TOKEN_AUTH, transport=rec.transport)
    assert len(rec.requests) == 50  # no probe with a token


def test_a_401_on_the_basic_probe_switches_to_a_bearer_token():
    def answer(r):
        if r.headers["authorization"] == BASIC:
            return httpx.Response(401, json={"message": "no"})
        assert r.headers["authorization"] == BEARER
        return page([{"name": "supplementary_data.csv", "totalBytes": 7}])

    rec = Recorder(answer)
    assert bdb.list_files(auth=AUTH, transport=rec.transport) == [
        {"name": "supplementary_data.csv", "bytes": 7}
    ]
    auths = [r.headers["authorization"] for r in rec.requests]
    assert auths[0] == BASIC and set(auths[1:]) == {BEARER} and len(auths) >= 2


@pytest.mark.parametrize("auth", [AUTH, TOKEN_AUTH])
def test_a_refused_credential_is_an_error_that_does_not_show_the_secret(auth):
    rec = Recorder(lambda r: httpx.Response(401, text="nope testkey testtoken"))
    with pytest.raises(bdb.KaggleError) as e:
        bdb.list_files(auth=auth, transport=rec.transport)
    assert "refused the credentials" in str(e.value) and no_secret(str(e.value))
    assert "KAGGLE_API_TOKEN" in str(e.value)


@pytest.mark.parametrize("auth", [AUTH, TOKEN_AUTH])
@pytest.mark.parametrize(
    ("status", "words"),
    [
        (403, "join the competition on kaggle.com and accept its rules"),
        (404, "no such competition or file"),
        (500, "Kaggle answered HTTP 500"),
        (429, "Kaggle answered HTTP 429"),
    ],
)
def test_list_files_errors_say_what_to_do_and_never_carry_the_secret(auth, status, words):
    def answer(r):
        if r.url.params.get("pageSize") == "1":
            return page([])
        return httpx.Response(status, text="body mentions testkey testtoken")

    with pytest.raises(bdb.KaggleError) as e:
        bdb.list_files(auth=auth, transport=Recorder(answer).transport)
    assert words in str(e.value) and str(e.value).startswith("listing the files:")
    assert no_secret(str(e.value))


# ---- the names Kaggle gives ----------------------------------------------------------------------

LISTED = [
    {"name": "supplementary_data.csv", "bytes": 1},
    {"name": "train/input_2023_w01.csv", "bytes": 2},
    {"name": "train/output_2023_w01.csv", "bytes": 3},
    {"name": "train/input_2023_w02.csv", "bytes": 4},
]


def test_kaggle_names_gives_the_full_name_of_each_wanted_file_in_order():
    got = bdb.kaggle_names(LISTED, ["output_2023_w01.csv", "supplementary_data.csv"])
    assert got == ["train/output_2023_w01.csv", "supplementary_data.csv"]
    assert bdb.kaggle_names(LISTED, []) == []
    assert bdb.kaggle_names([], []) == []


def test_kaggle_names_names_every_missing_file_and_never_returns_a_partial_list():
    with pytest.raises(bdb.KaggleError) as e:
        bdb.kaggle_names(LISTED, ["input_2023_w01.csv", "output_2023_w02.csv", "players.csv"])
    msg = str(e.value)
    assert "output_2023_w02.csv" in msg and "players.csv" in msg and "input_2023_w01.csv" not in msg
    assert bdb.COMPETITION in msg


def test_the_files_the_cli_wants_resolve_against_the_listing():
    listed = [{"name": "supplementary_data.csv", "bytes": 1}] + [
        {"name": f"train/{n}", "bytes": 1} for n in bdb.week_files([1, 2])
    ]
    got = bdb.kaggle_names(listed, [*bdb.NEEDED, *bdb.week_files([2])])
    assert got == [
        "supplementary_data.csv",
        "train/input_2023_w02.csv",
        "train/output_2023_w02.csv",
    ]


# ---- download ------------------------------------------------------------------------------------


def file_server(contents: dict[str, bytes]):
    """Serves `contents` by the decoded name in the URL (Kaggle's full name)."""

    def answer(r):
        if r.url.path == LIST_PATH:
            return page([])
        name = r.url.raw_path.decode().split("?")[0].removeprefix(DL_PATH)
        name = name.replace("%2F", "/")
        if name in contents:
            return httpx.Response(200, content=contents[name])
        return httpx.Response(404)

    return Recorder(answer)


def zipped(data: bytes, member: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(member, data)
    return buf.getvalue()


def dl(paths, names, rec, auth=AUTH, log=None):
    return bdb.download(
        paths, names, auth=auth, transport=rec.transport, log=log or (lambda s: None)
    )


def test_a_download_saves_by_the_files_own_name_and_keeps_kaggles_in_the_manifest(paths):
    rec = file_server(
        {"supplementary_data.csv": b"game_id\n1\n", "train/input_2023_w01.csv": b"frame_id\n9\n"}
    )
    log: list[str] = []
    done = dl(paths, ["supplementary_data.csv", "train/input_2023_w01.csv"], rec, log=log.append)
    raw = bdb.raw_dir(paths)
    assert (raw / "supplementary_data.csv").read_bytes() == b"game_id\n1\n"
    assert (raw / "input_2023_w01.csv").read_bytes() == b"frame_id\n9\n"  # no folder on disk
    assert [d["name"] for d in done] == ["supplementary_data.csv", "input_2023_w01.csv"]
    man = json.loads((raw / "manifest.json").read_text("utf-8"))
    assert set(man["files"]) == {"supplementary_data.csv", "input_2023_w01.csv"}
    entry = man["files"]["input_2023_w01.csv"]
    assert entry["bytes"] == 11 and entry["sha256"] == hashlib.sha256(b"frame_id\n9\n").hexdigest()
    assert entry["kaggle_name"] == "train/input_2023_w01.csv"
    assert entry["competition"] == "nfl-big-data-bowl-2026-analytics"
    assert entry["downloaded"].endswith("+00:00")
    assert man["files"]["supplementary_data.csv"]["kaggle_name"] == "supplementary_data.csv"
    assert any("input_2023_w01.csv" in s and "MB" in s for s in log)
    assert not any("train/" in s for s in log)
    # no partial files are left, and the secret is nowhere
    assert sorted(p.name for p in raw.iterdir()) == [
        "input_2023_w01.csv",
        "manifest.json",
        "supplementary_data.csv",
    ]
    assert no_secret((raw / "manifest.json").read_text("utf-8") + " ".join(log))
    assert {r.headers["authorization"] for r in rec.requests} == {BASIC}


def test_the_kaggle_name_is_url_encoded_into_one_path_segment(paths):
    rec = file_server({"train/input_2023_w01.csv": b"x"})
    dl(paths, ["train/input_2023_w01.csv"], rec)
    asked = [p for p in rec.raw_paths if p.startswith(DL_PATH)]
    assert asked == [DL_PATH + "train%2Finput_2023_w01.csv"]


def test_a_token_download_is_bearer_with_no_probe(paths):
    rec = file_server({"supplementary_data.csv": b"x"})
    dl(paths, ["supplementary_data.csv"], rec, auth=TOKEN_AUTH)
    assert len(rec.requests) == 1 and rec.requests[0].url.path.startswith(DL_PATH)
    assert rec.requests[0].headers["authorization"] == BEARER_TOKEN


def test_a_zipped_answer_is_unpacked_under_the_files_name(paths):
    data = b"game_id,week\n1,1\n"
    rec = file_server(
        {
            "supplementary_data.csv": zipped(data, "supplementary_data.csv"),
            "train/input_2023_w02.csv": zipped(b"p\n", "sub/dir/input_2023_w02.csv"),
        }
    )
    dl(paths, ["supplementary_data.csv", "train/input_2023_w02.csv"], rec)
    raw = bdb.raw_dir(paths)
    assert (raw / "supplementary_data.csv").read_bytes() == data
    assert (raw / "input_2023_w02.csv").read_bytes() == b"p\n"
    man = json.loads((raw / "manifest.json").read_text("utf-8"))
    assert man["files"]["supplementary_data.csv"]["bytes"] == len(data)  # the unpacked size
    assert sorted(p.name for p in raw.iterdir()) == [
        "input_2023_w02.csv",
        "input_2023_w02.csv.zip",
        "manifest.json",
        "supplementary_data.csv",
        "supplementary_data.csv.zip",
    ]


def test_a_zip_with_a_hostile_member_name_cannot_write_outside_the_raw_folder(paths):
    rec = file_server({"supplementary_data.csv": zipped(b"evil\n", "../../../evil.csv")})
    dl(paths, ["supplementary_data.csv"], rec)
    raw = bdb.raw_dir(paths)
    assert (raw / "supplementary_data.csv").read_bytes() == b"evil\n"
    assert not list(paths.root.rglob("evil.csv"))


def test_a_name_with_dot_dot_folders_is_saved_by_its_file_name_inside_the_raw_folder(paths):
    rec = file_server({"../../evil.csv": b"x"})
    dl(paths, ["../../evil.csv"], rec)
    raw = bdb.raw_dir(paths)
    assert (raw / "evil.csv").read_bytes() == b"x"
    assert not [p for p in paths.root.rglob("evil.csv") if p.parent != raw]


def test_files_already_on_disk_are_skipped_without_a_request(paths):
    rec = file_server({"train/input_2023_w01.csv": b"abc"})
    dl(paths, ["train/input_2023_w01.csv"], rec)
    before = len(rec.requests)
    log: list[str] = []
    done = dl(paths, ["train/input_2023_w01.csv"], rec, log=log.append)
    assert done == [] and log == ["input_2023_w01.csv: already on disk"]
    assert [p for p in rec.paths[before:] if p.startswith(DL_PATH)] == []


def test_a_file_whose_size_changed_is_fetched_again_and_the_manifest_keeps_the_others(paths):
    rec = file_server({"supplementary_data.csv": b"abc", "train/input_2023_w01.csv": b"12345"})
    dl(paths, ["supplementary_data.csv"], rec)
    dl(paths, ["train/input_2023_w01.csv"], rec)
    raw = bdb.raw_dir(paths)
    man = json.loads((raw / "manifest.json").read_text("utf-8"))
    assert set(man["files"]) == {"supplementary_data.csv", "input_2023_w01.csv"}
    (raw / "supplementary_data.csv").write_bytes(b"truncated!!")  # not the recorded size
    done = dl(paths, ["supplementary_data.csv"], rec)
    assert [d["name"] for d in done] == ["supplementary_data.csv"]
    assert (raw / "supplementary_data.csv").read_bytes() == b"abc"
    (raw / "output_2023_w01.csv").write_bytes(b"x")  # on disk but never recorded
    rec2 = file_server({"train/output_2023_w01.csv": b"yy"})
    dl(paths, ["train/output_2023_w01.csv"], rec2)
    assert (raw / "output_2023_w01.csv").read_bytes() == b"yy"


@pytest.mark.parametrize("auth", [AUTH, TOKEN_AUTH])
@pytest.mark.parametrize(
    ("status", "words"),
    [(404, "no such competition or file"), (403, "accept its rules"), (500, "HTTP 500")],
)
def test_a_failed_download_names_the_file_and_leaves_no_partial_file(paths, auth, status, words):
    def answer(r):
        if r.url.path == LIST_PATH:
            return page([])
        return httpx.Response(status, text="testkey testtoken in the body")

    with pytest.raises(bdb.KaggleError) as e:
        dl(paths, ["train/input_2023_w01.csv"], Recorder(answer), auth=auth)
    assert str(e.value).startswith("downloading input_2023_w01.csv:") and words in str(e.value)
    assert no_secret(str(e.value))
    raw = bdb.raw_dir(paths)
    assert not (raw / "input_2023_w01.csv").exists() and not list(raw.glob(".*"))
    assert not (raw / "manifest.json").exists()


def test_files_before_a_failure_stay_recorded(paths):
    rec = file_server({"supplementary_data.csv": b"ok"})  # the next one is a 404
    with pytest.raises(bdb.KaggleError, match="downloading input_2023_w01.csv"):
        dl(paths, ["supplementary_data.csv", "train/input_2023_w01.csv"], rec)
    raw = bdb.raw_dir(paths)
    assert (raw / "supplementary_data.csv").read_bytes() == b"ok"
    man = json.loads((raw / "manifest.json").read_text("utf-8"))
    assert set(man["files"]) == {"supplementary_data.csv"}


def test_a_401_on_a_basic_probe_downloads_with_bearer(paths):
    def answer(r):
        if r.headers["authorization"] == BASIC:
            return httpx.Response(401)
        if r.url.path == LIST_PATH:
            return page([])
        return httpx.Response(200, content=b"data")

    rec = Recorder(answer)
    dl(paths, ["train/output_2023_w03.csv"], rec)
    assert rec.requests[-1].headers["authorization"] == BEARER
    assert (bdb.raw_dir(paths) / "output_2023_w03.csv").read_bytes() == b"data"


# ---- Sol's review fixes (PC03) -------------------------------------------------------------------
# credentials an HTTP header can't carry never reach the transport, and an HTTP error becomes a
# fixed-text KaggleError that names only its type


def calls(paths, auth, rec):
    """The two Kaggle calls that sign in: a listing and a download."""
    return (
        lambda: bdb.list_files(auth=auth, transport=rec.transport),
        lambda: bdb.download(
            paths,
            ["supplementary_data.csv"],
            auth=auth,
            transport=rec.transport,
            log=lambda s: None,
        ),
    )


BAD_SECRETS = [
    "tok\nsecret",
    "tok secret",
    "tok\tsecret",
    "tok\r\nsecret",
    "tok\x00secret",
    "tok\x1fsecret",
    "tok\x7fsecret",
    "tok secret",  # a no-break space
    "tok secret",  # an em space
    "tok secret",  # a line separator
    "\ntoksecret",  # a break at the edge too
    "toksecret\n",
]


@pytest.mark.parametrize("secret", BAD_SECRETS, ids=lambda s: repr(s))
@pytest.mark.parametrize("shape", ["token", "key", "username"])
def test_credentials_with_a_space_or_control_character_never_reach_the_transport(
    paths, secret, shape
):
    auth = {"token": ("", secret), "key": ("user", secret), "username": (secret, "key")}[shape]
    rec = Recorder(lambda r: page([]))
    for call in calls(paths, auth, rec):
        with pytest.raises(bdb.KaggleError) as e:
            call()
        msg = str(e.value)
        assert "a space" in msg and "a line break" in msg and "the value isn't shown" in msg
        assert "tok" not in msg and "secret" not in msg
        assert e.value.__cause__ is None
    assert rec.requests == []  # nothing was sent, not even the probe
    assert not bdb.raw_dir(paths).exists() or not list(bdb.raw_dir(paths).iterdir())


@pytest.mark.parametrize(
    "auth",
    [
        ("", "KGAT_abc123.DEF-xyz_789~+/="),
        ("user", "0123456789abcdef0123456789abcdef"),
        ("user.name-1_x", "k"),
    ],
)
def test_credentials_an_http_header_can_carry_go_through(auth):
    rec = Recorder(lambda r: page([{"name": "supplementary_data.csv", "totalBytes": 1}]))
    assert bdb.list_files(auth=auth, transport=rec.transport)[0]["name"] == "supplementary_data.csv"
    assert rec.requests


@pytest.mark.parametrize("auth", [("", "tökén123"), ("", "tok€")], ids=["accents", "euro sign"])
def test_a_token_a_header_cannot_encode_is_refused_with_fixed_text(paths, auth):
    """Non-ASCII in a Bearer token would blow up inside the HTTP stack with a message that
    quotes the character and its position."""
    rec = Recorder(lambda r: page([]))
    for call in calls(paths, auth, rec):
        with pytest.raises(bdb.KaggleError) as e:
            call()
        msg = str(e.value)
        assert "ö" not in msg and "€" not in msg and "position" not in msg
    assert rec.requests == []


HTTP_ERRORS = [
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.LocalProtocolError,
    httpx.UnsupportedProtocol,
    httpx.TooManyRedirects,
    httpx.DecodingError,
]


@pytest.mark.parametrize("exc", HTTP_ERRORS, ids=lambda e: e.__name__)
@pytest.mark.parametrize("auth", [AUTH, TOKEN_AUTH], ids=["basic", "token"])
def test_an_http_error_becomes_a_kaggle_error_that_names_only_its_type(paths, exc, auth):
    def boom(request):
        raise exc("Authorization: Bearer testkey testtoken " + str(request.url))

    rec = Recorder(boom)
    with pytest.raises(bdb.KaggleError) as e:
        bdb.list_files(auth=auth, transport=rec.transport)
    assert str(e.value) == f"listing the files: the request failed ({exc.__name__})"
    assert e.value.__cause__ is None and e.value.__suppress_context__ is True
    assert no_secret(str(e.value)) and "http" not in str(e.value).lower()
    with pytest.raises(bdb.KaggleError) as e2:
        dl(paths, ["supplementary_data.csv"], Recorder(boom), auth=auth)
    assert str(e2.value) == f"downloading: the request failed ({exc.__name__})"
    assert e2.value.__cause__ is None and no_secret(str(e2.value))


def test_an_error_on_a_later_page_or_file_is_the_same_clean_error(paths):
    state = {"n": 0}

    def second_page_dies(r):
        state["n"] += 1
        if state["n"] == 3:  # probe, page 1, then page 2 fails
            raise httpx.ReadTimeout("slow testkey")
        return page([{"name": "a.csv", "totalBytes": 1}], token="more")

    with pytest.raises(
        bdb.KaggleError, match=r"listing the files: the request failed \(ReadTimeout\)"
    ):
        bdb.list_files(auth=AUTH, transport=Recorder(second_page_dies).transport)

    def second_file_dies(r):
        if r.url.path == LIST_PATH:
            return page([])
        if "input_2023_w01" in r.url.raw_path.decode():
            raise httpx.ConnectError("refused testkey")
        return httpx.Response(200, content=b"ok")

    with pytest.raises(bdb.KaggleError, match=r"downloading: the request failed \(ConnectError\)"):
        dl(
            paths,
            ["supplementary_data.csv", "train/input_2023_w01.csv"],
            Recorder(second_file_dies),
        )
    raw = bdb.raw_dir(paths)
    assert (raw / "supplementary_data.csv").read_bytes() == b"ok"  # the first one is kept
    assert not (raw / "input_2023_w01.csv").exists()


class DiesMidBody(httpx.SyncByteStream):
    def __iter__(self):
        yield b"abc"
        raise httpx.ReadError("connection reset testkey")


def mid_body_answer(r):
    if r.url.path == LIST_PATH:
        return page([])
    return httpx.Response(200, stream=DiesMidBody())


def test_a_download_that_dies_mid_body_leaves_no_file_and_no_manifest_entry(paths):
    with pytest.raises(bdb.KaggleError, match=r"downloading: the request failed \(ReadError\)"):
        dl(paths, ["supplementary_data.csv"], Recorder(mid_body_answer))
    raw = bdb.raw_dir(paths)
    assert not (raw / "supplementary_data.csv").exists()
    assert not (raw / "manifest.json").exists()


def test_a_download_that_dies_mid_body_does_not_leave_its_partial_file_behind(paths):
    with pytest.raises(bdb.KaggleError):
        dl(paths, ["supplementary_data.csv"], Recorder(mid_body_answer))
    assert not list(bdb.raw_dir(paths).glob(".*part"))


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>Sign in</html>"),
        httpx.Response(200, json=[1, 2, "x"]),
        httpx.Response(200, json={"files": [1, 2]}),
        httpx.Response(200, json="a string"),
        httpx.Response(200, content=b""),
    ],
    ids=["html", "list of ints", "files of ints", "a string", "empty"],
)
def test_an_answer_that_is_not_a_file_list_is_a_kaggle_error_not_a_traceback(response):
    rec = Recorder(lambda r: response)
    with pytest.raises(bdb.KaggleError) as e:
        bdb.list_files(auth=TOKEN_AUTH, transport=rec.transport)
    assert str(e.value).startswith("listing the files:") and no_secret(str(e.value))
