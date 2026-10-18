"""`safetext.scrub_bounded` (AE01, D142): the same masks as `ops.summary.scrub`, in bounded time."""

from __future__ import annotations

import random
import string
import time

import pytest

from nflengine.ops.summary import scrub
from nflengine.safetext import MARGIN, scrub_bounded

WORDS = [
    "api_key", "token", "Bearer", "basic", "password", "passwd", "x-auth", "my-secret",
    "credential", "key", "sk-or-v1-abcdefgh1234", "wandb_v1_abcdefghij", "ghp_" + "a" * 20,
    "http://u:p@h/x?a=1", "https://host/p?q=2", "a" * 40, "=", ":", '"', "'", " ", ",", ";",
    "}", "]", "value", "abc", "-", "_", "\n", "monkey", "author",
]  # fmt: skip


def random_texts(n: int, seed: int = 7):
    rnd = random.Random(seed)
    for _ in range(n):
        s = "".join(
            rnd.choice(WORDS) + rnd.choice(["", " ", "=", ": "]) for _ in range(rnd.randint(1, 12))
        )
        yield s + "".join(rnd.choice(string.printable) for _ in range(rnd.randint(0, 10)))


def test_scrub_bounded_matches_ops_scrub():
    """Below the cap, the output is exactly `ops.summary.scrub`'s: the anchored name=value
    rule and the skipped URL rules mask the same things."""
    for s in random_texts(20_000):
        assert scrub_bounded(s, 10**6) == scrub(s, 10**6), repr(s)


@pytest.mark.parametrize("limit", [40, 400, 1000])
def test_short_texts_are_capped_like_ops_scrub(limit):
    for s in random_texts(2_000, seed=limit):
        assert scrub_bounded(s, limit) == scrub(s, limit), repr(s)


@pytest.mark.parametrize(
    "text",
    [
        "x" * 5_000_000,
        "key" + "x" * 5_000_000,
        "a." * 2_000_000,
        "http://" + "a." * 2_000_000 + "?q=1",
        "a" * 2_000_000 + "g",
    ],
    ids=["word", "key-word", "dots", "url-dots", "hex-then-word"],
)
def test_a_huge_text_is_cleaned_in_bounded_time(text):
    t0 = time.perf_counter()
    out = scrub_bounded(text, 1000)
    assert time.perf_counter() - t0 < 0.5
    assert len(out) <= 1000 and out.endswith("…")


def test_a_secret_cut_at_the_cap_is_never_shown_half_masked():
    # a long hex key straddling the input cap: the word the cut fell in is never shown
    secret = "ab" * 40  # 80 hex characters
    text = "x " * ((1000 + MARGIN - 20) // 2) + secret
    out = scrub_bounded(text, 1000)
    assert "abab" not in out
    assert scrub(out, 10**6) == out


def test_a_url_userinfo_whose_at_sign_is_past_the_cap_is_masked():
    # Sol (AE01): "@" outside the inspected prefix used to leave the credential visible
    text = "see https://u:" + "z" * 2000 + "@example.test/x"
    out = scrub_bounded(text, 400)
    assert "zzzz" not in out and out.startswith("see https://***")
    assert scrub(text, 400).startswith("see https://***")  # what the original shows


def test_a_quoted_secret_whose_closing_quote_is_past_the_cap_is_masked():
    # Sol (AE01): the unquoted rule masked only up to the first space of a long quoted value
    text = 'password="first ' + "word " * 600 + 'last"'
    out = scrub_bounded(text, 400)
    assert " word" not in out and "first" not in out and out.startswith("password=***")


@pytest.mark.parametrize(
    "construct",
    [
        'password="SECRETa SECRETb SECRETc SECRETd"',
        "api_key='SECRETa SECRETb SECRETc'",
        "token = SECRETaSECRETbSECRETc",
        "https://user:SECRETaSECRETbSECRETc@host.example/x",
        "Bearer SECRETaSECRETbSECRETcSECRETd",
        "sk-or-v1-SECRETaSECRETbSECRETc",
        "x " + "ab12" * 20 + " y",  # a 80-character hex key
    ],
)
def test_no_piece_of_a_secret_shows_wherever_the_cap_falls(construct):
    """Slide the secret across the input cap one character at a time: whatever the original
    masks, the bounded scrub never shows a piece of it."""
    limit = 200
    for pad in range(limit - 80, limit + MARGIN + 10):
        text = "y " * (pad // 2) + " " * (pad % 2) + construct + " tail" * 200
        out = scrub_bounded(text, limit)
        assert "SECRET" not in out, (pad, out[-120:])
        assert "ab12ab12ab12" not in out.replace("ab12" * 20, ""), pad
        if "ab12" * 20 in text[: limit - 1] and "ab12" * 20 in out:
            pytest.fail(f"hex key shown at pad {pad}")


def test_an_inner_unclosed_value_cannot_take_an_outer_values_closing_quote():
    # Sol's verification (AE01): `token='third` (unclosed) used to remove the password's quote
    text = 'password="first second token=\'third" ' + "x " * 1000
    out = scrub_bounded(text, 400)
    assert "second" not in out and "first" not in out and "third" not in out
    assert out.startswith("password=***")


def test_masks_survive_the_cap():
    out = scrub_bounded("token=sk-or-v1-SECRETSECRET " + "y " * 3000, 200)
    assert "SECRET" not in out and out.startswith("token=***")
