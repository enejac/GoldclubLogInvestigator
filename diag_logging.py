"""
Diagnostic file logging for fleet UI, clock drift refresh, and remote time sync.

Logs go under %LOCALAPPDATA%\\GoldclubLogInvestigator\\logs\\fleet_timesync.log
so Windows heap/native crashes (e.g. 0xc0000374 in ntdll) can be correlated with
the last Python-side operations.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_LOGGER_NAME = "goldclub.fleet_timesync"
_configured = False


def fleet_timesync_logger() -> logging.Logger:
    """Lazily attach a file handler once; safe to call from GUI or network code."""
    global _configured
    log = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return log

    log.setLevel(logging.DEBUG)
    log.propagate = False
    try:
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        log_dir = base / "GoldclubLogInvestigator" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "fleet_timesync.log"
        fh = logging.FileHandler(path, mode="a", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        log.addHandler(fh)
        log.info(
            "Fleet/time-sync diagnostic logging enabled (path=%s, pid=%s)",
            path.resolve(),
            os.getpid(),
        )
    except OSError as e:
        logging.getLogger(__name__).warning("fleet_timesync file log unavailable: %s", e)
        log.propagate = True
    finally:
        _configured = True
    return log
