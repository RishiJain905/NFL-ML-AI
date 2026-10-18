"""`ops.summary.scrub`'s name=value rule anchored at the start of a word (AE01, D144; Rishi's OK).

The old rule retried `[\\w-]*` from every start position: quadratic on a long word (20,000
characters took up to 28 s). The anchored rule must give exactly the old output."""

from __future__ import annotations

import random
import re
import string
import time

import pytest

from nflengine.ops import summary
from nflengine.ops.summary import scrub

OLD_SECRET_KV = re.compile(
    r"(?i)([\"']?)([\w-]*(?:key|token|secret|password|passwd|auth|credential)[\w-]*)\1"
    r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
WORDS = [
    "api_key", "token", "Bearer", "basic", "password", "passwd", "x-auth", "my-secret",
    "credential", "key", "KEY", "monkey", "author", "sk-or-v1-abcdefgh1234", "wandb_v1_abcdefghij",
    "http://u:p@h/x?a=1", "a" * 40, "=", ":", '"', "'", " ", ",", ";", "}", "]", "value", "abc",
    "-", "_", "\n", "x", "9",
]  # fmt: skip


def old_scrub(text: str, limit: int = 10**6) -> str:
    s = "" if text is None else str(text)
    s = summary._URL_QUERY.sub(r"\1?…", s)
    s = summary._URL_USERINFO.sub(r"\1***@", s)
    s = summary._AUTH_SCHEME.sub(lambda m: f"{m.group(1)} ***", s)
    s = OLD_SECRET_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(1)}{m.group(3)}***", s)
    s = summary._KEY_SHAPES.sub("***", s)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def test_the_anchored_rule_gives_the_old_output():
    rnd = random.Random(144)
    for _ in range(30_000):
        s = "".join(
            rnd.choice(WORDS) + rnd.choice(["", " ", "=", ": ", "'", '"'])
            for _ in range(rnd.randint(1, 14))
        )
        s += "".join(rnd.choice(string.printable) for _ in range(rnd.randint(0, 12)))
        assert scrub(s, 10**6) == old_scrub(s), repr(s)


@pytest.mark.parametrize(
    "text",
    [
        "api_key=abc",
        'x"token": "value with spaces"',
        "'password'='p w'",
        "monkey=banana",
        "my-auth-header: Bearer abcdefgh",
        'a"key"="b"',
        "xkey=1 ykey=2",
        "key==2",
    ],
)
def test_known_shapes_are_unchanged(text):
    assert scrub(text, 10**6) == old_scrub(text)


def test_a_long_word_is_scrubbed_in_linear_time():
    t0 = time.perf_counter()
    out = scrub("x" * 200_000, 10**6)
    assert time.perf_counter() - t0 < 0.5 and out == "x" * 200_000
