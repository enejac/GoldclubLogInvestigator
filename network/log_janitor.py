"""
Archive and delete old log files under a cabinet log root (typically UNC).

Runs on a ``QThread`` from the Fleet tab; core pass is also callable for tests.
"""

from __future__ import annotations

import logging
import os
import time
import zipfile
from collections.abc import Callable
from typing import Final

from PySide6.QtCore import QObject, QThread, Signal

from config_manager import SettingsManager

logger = logging.getLogger(__name__)

_SECONDS_PER_DAY: Final[float] = 86400.0


def _age_days(path: str, now: float) -> float:
    try:
        mtime = os.path.getmtime(path)
    except OSError as e:
        logger.debug("mtime failed for %s: %s", path, e)
        return -1.0
    return max(0.0, (now - mtime) / _SECONDS_PER_DAY)


def _unique_zip_path(log_path: str) -> str:
    directory, base = os.path.split(log_path)
    stem, _ext = os.path.splitext(base)
    candidate = os.path.join(directory, f"{stem}.log.zip")
    n = 0
    while os.path.exists(candidate):
        n += 1
        candidate = os.path.join(directory, f"{stem}_{n}.log.zip")
    return candidate


def run_janitor_pass(
    root: str,
    *,
    archive_days: int | None = None,
    delete_days: int | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
    interrupt_check: Callable[[], bool] | None = None,
) -> tuple[int, int]:
    """
    Walk ``root`` for ``.log`` / ``.zip`` files.

    * Age > ``delete_days``: delete file (``.log`` or ``.zip``).
    * Else age > ``archive_days`` and ``.log``: zip then remove original.

    Returns ``(freed_bytes, files_touched)`` where *freed* is best-effort net
    bytes recovered (delete = full size; archive = max(0, log_size - zip_size)).

    When ``archive_days`` / ``delete_days`` are ``None``, values come from
    ``SettingsManager`` (user preferences).
    """
    ad = SettingsManager.get_log_archive_days() if archive_days is None else archive_days
    dd = SettingsManager.get_log_delete_days() if delete_days is None else delete_days

    root = os.path.abspath(os.path.normpath(root))
    if not os.path.isdir(root):
        logger.warning("Janitor root is not a directory: %s", root)
        if progress_callback:
            progress_callback(100, "Path is not reachable or not a directory.")
        return 0, 0

    targets: list[str] = []
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            if interrupt_check and interrupt_check():
                break
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext in (".log", ".zip"):
                    targets.append(os.path.join(dirpath, fn))
    except OSError as e:
        logger.warning("Janitor walk failed under %s: %s", root, e)
        if progress_callback:
            progress_callback(100, f"Walk error: {e}")
        return 0, 0

    total = len(targets)
    if total == 0:
        if progress_callback:
            progress_callback(100, "No .log or .zip files found.")
        return 0, 0

    now = time.time()
    freed_bytes = 0
    files_touched = 0

    for i, path in enumerate(targets):
        if interrupt_check and interrupt_check():
            if progress_callback:
                progress_callback(100, "Stopped (interrupted).")
            break
        pct = int(100 * (i + 1) / total) if total else 100
        rel = os.path.relpath(path, root)
        if progress_callback:
            progress_callback(pct, rel)

        ext = os.path.splitext(path)[1].lower()
        age = _age_days(path, now)
        if age < 0:
            continue

        if age > dd:
            try:
                sz = os.path.getsize(path)
                os.remove(path)
                freed_bytes += sz
                files_touched += 1
            except OSError as e:
                logger.warning("Janitor delete failed %s: %s", path, e)
            continue

        if age > ad and ext == ".log":
            try:
                log_sz = os.path.getsize(path)
            except OSError:
                continue
            zip_path = _unique_zip_path(path)
            try:
                with zipfile.ZipFile(
                    zip_path, "w", compression=zipfile.ZIP_DEFLATED
                ) as zf:
                    zf.write(path, arcname=os.path.basename(path))
                os.remove(path)
                zip_sz = os.path.getsize(zip_path)
                freed_bytes += max(0, log_sz - zip_sz)
                files_touched += 1
            except OSError as e:
                logger.warning("Janitor archive failed %s: %s", path, e)
                if os.path.isfile(zip_path):
                    try:
                        os.remove(zip_path)
                    except OSError:
                        pass

    return freed_bytes, files_touched


class LogJanitorWorker(QThread):
    """Background janitor for one cabinet log root (e.g. UNC admin share)."""

    progress = Signal(int, str)
    finished = Signal(int, int)

    def __init__(self, target_unc_path: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root = (target_unc_path or "").strip()

    def run(self) -> None:
        freed, n = run_janitor_pass(
            self._root,
            progress_callback=lambda p, m: self.progress.emit(p, m),
            interrupt_check=self.isInterruptionRequested,
        )
        self.finished.emit(freed, n)
