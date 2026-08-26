"""Install directory and app-local writable paths (dev tree + frozen exe)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_install_dir() -> Path:
    """Folder containing LogInvestigator.exe, or repository root when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def is_network_path(path: Path | str) -> bool:
    """True for UNC paths (``\\\\server\\share\\…``) that keep SMB file locks open.

    Writing logs / temp next to an exe on ``\\\\10.0.0.90\\USB_Remote\\…`` holds the
    share open for the whole session, so Explorer reports "Folder In Use" when
    the operator tries to delete or rename the stick share.
    """
    s = str(path or "").strip().replace("/", "\\")
    if not s:
        return False
    if s.startswith("\\\\"):
        # \\?\C:\… is local; \\?\UNC\server\… is not.
        if s.startswith("\\\\?\\UNC\\") or s.startswith("\\\\.\\UNC\\"):
            return True
        if len(s) >= 4 and s[2] == "?" and s[3] == "\\":
            return False
        return True
    return False


def app_local_data_dir(*, mkdir: bool = True) -> Path:
    """Per-user local folder for writables when the install tree is on a share."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or str(Path.home())
    path = Path(base) / "LogInvestigator"
    if mkdir:
        path.mkdir(parents=True, exist_ok=True)
    return path


def app_writable_dir(*, mkdir: bool = True) -> Path:
    """Directory for logs, run packs and scratch files.

    Beside the exe on a local disk; under ``%LOCALAPPDATA%\\LogInvestigator`` when
    the exe (or repo) lives on a UNC share so we never leave SMB handles open.
    """
    install = app_install_dir()
    if is_network_path(install):
        return app_local_data_dir(mkdir=mkdir)
    if mkdir:
        try:
            install.mkdir(parents=True, exist_ok=True)
        except OSError:
            return app_local_data_dir(mkdir=mkdir)
    return install


def app_tmp_logs_dir(*, mkdir: bool = True) -> Path:
    """``_tmp_logs`` next to the writable root (Bug Detector, scratch logs)."""
    path = app_writable_dir(mkdir=mkdir) / "_tmp_logs"
    if mkdir:
        path.mkdir(parents=True, exist_ok=True)
    return path


def app_runs_dir(*, mkdir: bool = True) -> Path:
    """``automation_runs`` next to the writable root, never the current directory.

    A run pack has to end up somewhere the operator can find again. Resolved from
    the working directory it would land wherever the exe happened to be started
    from, which for a shortcut is ``C:\\Windows\\System32``.
    """
    path = app_writable_dir(mkdir=mkdir) / "automation_runs"
    if mkdir:
        path.mkdir(parents=True, exist_ok=True)
    return path


def bug_detector_output_base(*, mkdir: bool = True) -> Path:
    path = app_tmp_logs_dir(mkdir=mkdir) / "bug-detector"
    if mkdir:
        path.mkdir(parents=True, exist_ok=True)
    return path
