"""Small file-system helpers shared by the pipeline's writers.

`replace_file` (CR02): the pipeline writes its files as `tmp` → replace, so a crash never
leaves half a file. On Windows that replace fails with `PermissionError` while another
process has the target open without delete sharing, which Python's `open()` never grants.
The control room reads the run's files while a run is live (short reads, never a held
handle, D100), so a clash is rare but possible; a short retry makes it harmless.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

REPLACE_TRIES = 40
REPLACE_WAIT_S = 0.05  # 40 × 50 ms: a reader's open lasts milliseconds


def replace_file(src: Path | str, dst: Path | str, tries: int = REPLACE_TRIES) -> None:
    """`os.replace(src, dst)`, retried for about two seconds on `PermissionError`."""
    for attempt in range(max(1, tries)):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(REPLACE_WAIT_S)
