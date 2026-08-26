"""File logging beside the executable for GUI diagnostics."""

from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False
_LOG_PATH: Path | None = None


from app_paths import app_writable_dir


def app_log_dir() -> Path:
    """Directory for LogInvestigator.log.

    Uses the writable root (local AppData when the exe lives on a UNC share) so a
    rotating log handle never keeps ``\\\\cabinet\\USB_Remote`` locked.
    """
    return app_writable_dir()


def log_file_path(*, filename: str | None = None) -> Path:
    name = (filename or "").strip() or "LogInvestigator.log"
    return app_log_dir() / name


def configure_app_logging(
    *,
    level: int = logging.DEBUG,
    filename: str | None = None,
) -> Path:
    """Attach rotating file log + stderr; safe to call once at startup.

    Pass ``filename`` (e.g. ``SasVerifyMeters.log``) for standalone tools so the
    log sits next to that exe instead of sharing ``LogInvestigator.log``.
    """
    global _CONFIGURED, _LOG_PATH
    path = log_file_path(filename=filename)
    if _CONFIGURED:
        return _LOG_PATH or path

    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    if not getattr(sys, "frozen", False):
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(logging.INFO)
        stream.setFormatter(fmt)
        root.addHandler(stream)

    _install_exception_hooks(path)
    _CONFIGURED = True
    _LOG_PATH = path

    log = logging.getLogger("gui.app_logging")
    log.info(
        "Logging started path=%s frozen=%s executable=%s",
        path,
        getattr(sys, "frozen", False),
        sys.executable,
    )
    return path


def _install_exception_hooks(log_path: Path) -> None:
    def _log_unhandled(exc_type, exc, tb) -> None:
        if exc_type is KeyboardInterrupt:
            sys.__excepthook__(exc_type, exc, tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        logging.getLogger("gui.crash").critical("Unhandled exception:\n%s", text)
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _log_unhandled

    if hasattr(__import__("threading"), "excepthook"):
        def _thread_hook(args) -> None:  # noqa: ANN001
            logging.getLogger("gui.crash").critical(
                "Unhandled thread exception in %s:\n%s",
                getattr(args, "thread", None),
                "".join(
                    traceback.format_exception(
                        args.exc_type, args.exc_value, args.exc_traceback
                    )
                ),
            )

        __import__("threading").excepthook = _thread_hook  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    if not _CONFIGURED:
        configure_app_logging()
    return logging.getLogger(name)