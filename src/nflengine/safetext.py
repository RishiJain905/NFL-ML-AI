"""A bounded, linear-time scrub for text nobody controls (AE01, D142).

`ops.summary.scrub` masks credential-shaped text before anything is shown or stored, but it
scrubs the whole text before capping it, and its `name=value` rule (`[\\w-]*` before the
key / token / password ... alternation) is retried from every start position, so it is
quadratic on a long run of word characters: 10,000 characters took 3.5 s, 20,000 up to 28 s
(the AE01 test worker). A legal Ask query (`SELECT repeat('a', 5000000)`, a `string_agg`)
returns such a cell, and the control room cleans every cell, so one question could hold a
request thread (and Ask's one-question lock) for hours. `ops/` is protected by the no-touch
rule (D107), so the fix lives here and the Ask / control-room code calls it:

- the input is capped **before** scrubbing, at `limit + MARGIN` characters (the output is
  capped to `limit` anyway). A cut can leave a secret unfinished, and the original's decision
  may depend on text past the cut (Sol, AE01), so when the text was cut: a quoted secret value
  whose closing quote is past it is masked to the end (`password="first second ...`: the
  unquoted rule alone would mask only up to the first space), and so is a URL whose userinfo
  `@` is past it (`https://user:pass...`). The other rules run on the whole prefix: one whose
  span reaches the cut (an unquoted value, a long key-shaped string, a bearer token) is masked
  up to it, since the end of the text counts as a word boundary. What's left is a secret cut
  so close to the end that too little of it remains to match (a key cut 10 characters in), so
  **the last `TAIL` characters of a word the cut fell in are never shown** (nor a shorter
  word). The visible text equals the original's or masks more;
- the `name=value` rule starts only at the start of a word run (`(?<![\\w-])` after the
  optional quote): identical output to `ops.summary.scrub` on 60,000 random secret-shaped
  texts, and linear (5 MB of one word in 0.02 s);
- the URL rules run only when the text has "://" (and a "?" / "@" for the query / userinfo
  rule): they can't match otherwise, and on a run like "a.a.a..." they are still quadratic,
  which the input cap bounds (0.04 s at 1,000 characters, ~0.7 s at 12,000).

The other patterns are `ops.summary`'s own, imported read-only, so both scrubs mask the same
things; `test_scrub_bounded_matches_ops_scrub` keeps them in step.
"""

from __future__ import annotations

import re
from typing import Any

from nflengine.ops.summary import _AUTH_SCHEME, _KEY_SHAPES, _URL_QUERY, _URL_USERINFO

MARGIN = 256
TAIL = 128  # of an unfinished last word, never shown (< MARGIN: a long word still fills `limit`)
_SECRET_KV = re.compile(
    r"(?i)([\"']?)(?<![\w-])([\w-]*(?:key|token|secret|password|passwd|auth|credential)[\w-]*)\1"
    r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)


# a cut text: the same name=value rule, but a quoted value may also run to the end (its closing
# quote is past the cut). One left-to-right rule, so an inner `token='...` can't take an outer
# value's closing quote (Sol's verification: `password="a b token='c" ...` showed "b")
_SECRET_KV_CUT = re.compile(
    r"(?i)([\"']?)(?<![\w-])([\w-]*(?:key|token|secret|password|passwd|auth|credential)[\w-]*)\1"
    r"(\s*[=:]\s*)(\"[^\"]*(?:\"|\Z)|'[^']*(?:'|\Z)|[^\s,;}\]]+)"
)
# a cut text: an authority running to the end may be userinfo whose "@" is past the cut
_UNCLOSED_USERINFO = re.compile(r"(\b[a-z][\w+.-]*://)[^/@\s'\"<>]+\Z", re.I)


def scrub_bounded(text: Any, limit: int = 400) -> str:
    """`ops.summary.scrub(text, limit)`'s output, in time bounded by `limit`."""
    full = "" if text is None else str(text)
    cut = len(full) > limit + MARGIN
    s = full[: limit + MARGIN]
    masked_tail = False  # a cut text whose end was masked through (no unfinished word left)
    if cut and "://" in s:
        before = s
        s = _UNCLOSED_USERINFO.sub(r"\1***", s)
        masked_tail = s != before
    if "://" in s:
        if "?" in s:
            s = _URL_QUERY.sub(r"\1?…", s)
        if "@" in s:
            s = _URL_USERINFO.sub(r"\1***@", s)
    s = _AUTH_SCHEME.sub(lambda m: f"{m.group(1)} ***", s)

    def kv(m: re.Match) -> str:
        nonlocal masked_tail
        if cut and m.end() == len(m.string):
            masked_tail = True
        return f"{m.group(1)}{m.group(2)}{m.group(1)}{m.group(3)}***"

    s = (_SECRET_KV_CUT if cut else _SECRET_KV).sub(kv, s)
    s = _KEY_SHAPES.sub("***", s)
    if cut:
        # the cut fell inside a word: its last TAIL characters may hold a secret too short to
        # match yet (a key cut 10 characters in), so they're never shown (nor a shorter word)
        if not masked_tail and not full[limit + MARGIN].isspace():
            i = len(s)
            while i and not s[i - 1].isspace():
                i -= 1
            s = s[: max(i, len(s) - TAIL)]
        return s[: limit - 1] + "…"
    return s if len(s) <= limit else s[: limit - 1] + "…"
