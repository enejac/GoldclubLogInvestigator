"""
Scanner: discover `.log` / `.txt` files under configured roots (recursive).

Designed for local disks and UNC paths. Network and permission failures on an
active root surface as ``ScanRootError`` so the GUI can report them cleanly.
"""

from __future__ import annotations

import errno
import logging
from pathlib import Path
from typing import Iterator, Sequence

from config import LOG_EXTENSIONS

logger = logging.getLogger(__name__)


def _root_is_goldclub_var_log(root: Path) -> bool:
    """True if ``root`` looks like ``…\\Goldclub\\var\\log`` (or UNC equivalent)."""
    try:
        parts = [p.lower() for p in root.parts]
    except (OSError, ValueError):
        return False
    if len(parts) < 3:
        return False
    return parts[-3] == "goldclub" and parts[-2] == "var" and parts[-1] == "log"


def _is_logdaemon_priority_file(path: Path) -> bool:
    """Prefer ``GoldClub.Logging.LogDaemon`` logs for environment baseline before SlotLog / Aurum."""
    return any(p.lower() == "goldclub.logging.logdaemon" for p in path.parts)


class ScanRootError(OSError):
    """The scan root cannot be accessed (network, permissions, path invalid)."""


def _format_root_access_error(path: str | Path, exc: OSError) -> str:
    p = str(path)
    parts = [f"Cannot access scan root {p!r}: {exc}"]
    if exc.errno is not None:
        parts.append(f"errno={exc.errno}")
        try:
            parts.append(f"({errno.errorcode.get(exc.errno, 'unknown')})")
        except Exception:
            pass
    win = getattr(exc, "winerror", None)
    if win is not None:
        parts.append(f"WinError={win}")
    return " ".join(parts)


def iter_log_files(roots: Sequence[str | Path]) -> Iterator[Path]:
    """
    Yield file paths under each root whose suffix matches ``LOG_EXTENSIONS``.

    Permission errors on individual files are skipped. Failure to traverse a
    root (e.g. network dropped, ``WinError 53``) raises ``ScanRootError``.
    """
    ext_set = {e.lower() for e in LOG_EXTENSIONS}

    for raw in roots:
        root = Path(raw)
        try:
            exists = root.exists()
        except OSError as e:
            logger.error("Scan root access failed: %s", e)
            raise ScanRootError(_format_root_access_error(root, e)) from e

        if not exists:
            logger.warning("Scan root does not exist or is unreachable: %s", root)
            continue
        if root.is_file():
            if root.suffix.lower() in ext_set or root.name.lower() == "txrxdata.dat":
                try:
                    yield root.resolve()
                except OSError as e:
                    logger.debug("Skip file %s: %s", root, e)
            else:
                logger.warning("Scan root is not a log file: %s", root)
            continue
        if not root.is_dir():
            logger.warning("Scan root is not a directory: %s", root)
            continue

        try:
            for path in root.rglob("*"):
                try:
                    if not path.is_file():
                        continue
                    name_l = path.name.lower()
                    if path.suffix.lower() in ext_set or name_l == "txrxdata.dat":
                        yield path.resolve()
                except OSError as e:
                    logger.debug("Skip file %s: %s", path, e)
        except OSError as e:
            logger.error("Traversal failed for %s: %s", root, e)
            raise ScanRootError(_format_root_access_error(root, e)) from e


def collect_log_files(roots: Sequence[str | Path]) -> list[Path]:
    """
    Materialize ``iter_log_files`` (useful for progress reporting).

    If any root is under ``…\\Goldclub\\var\\log``, **LogDaemon** files are sorted first
    so the parser establishes the environment fingerprint before SlotLog / Aurum logs.
    """
    files = list(iter_log_files(roots))
    if any(_root_is_goldclub_var_log(Path(str(r))) for r in roots):
        return sorted(
            files,
            key=lambda p: (0 if _is_logdaemon_priority_file(p) else 1, str(p).lower()),
        )
    return files
