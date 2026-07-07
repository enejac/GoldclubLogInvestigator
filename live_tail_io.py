"""
Binary tail reads for live log watching (handles transient locks on Windows/UNC).

Tries normal shared read first, then falls back to copying to a temp file when
the source is temporarily locked (common with game engines writing logs).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from config import OPEN_RETRIES, OPEN_RETRY_DELAY_SEC

logger = logging.getLogger(__name__)


def read_new_bytes(path: Path, start: int, end: int) -> bytes:
    """
    Read ``[start, end)`` bytes from ``path``.

    Retries on sharing violations, then copies to a temp file and reads from
    there if direct read keeps failing.
    """
    if start < 0 or end < start:
        return b""
    length = end - start
    last_err: OSError | None = None

    for attempt in range(OPEN_RETRIES):
        try:
            with open(path, "rb", buffering=0) as handle:
                handle.seek(start)
                return handle.read(length)
        except OSError as e:
            last_err = e
            win = getattr(e, "winerror", None)
            if win in (32, 33, 5, 995):  # sharing, lock, access, aborted
                logger.debug(
                    "Tail read retry %s/%s for %s: %s",
                    attempt + 1,
                    OPEN_RETRIES,
                    path,
                    e,
                )
                time.sleep(OPEN_RETRY_DELAY_SEC)
                continue
            # Other errors: brief pause then retry; fall back to copy after loop
            logger.debug("Tail read error %s/%s for %s: %s", attempt + 1, OPEN_RETRIES, path, e)
            time.sleep(OPEN_RETRY_DELAY_SEC)

    # Fallback: copy to temp (may succeed when exclusive open does not)
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="gli_live_", suffix=".log")
        os.close(fd)
        shutil.copy2(path, tmp_path)
        with open(tmp_path, "rb", buffering=0) as handle:
            handle.seek(start)
            return handle.read(length)
    except OSError as e:
        logger.warning("Tail copy fallback failed for %s: %s", path, e)
        if last_err:
            raise last_err from e
        raise
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
