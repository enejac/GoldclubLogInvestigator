#!/usr/bin/env python3
"""
Launch the Log Investigator desktop UI (PySide6 / MVVM).

Run from the project directory:

    python gui_app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import gui.app_logging  # noqa: F401
from gui.main_window import run_app  # noqa: E402


if __name__ == "__main__":
    if "--config-scanner" in sys.argv:
        from gui.config_scanner_window import run_config_scanner_app  # noqa: E402

        raise SystemExit(run_config_scanner_app())
    if "--sas-verify" in sys.argv:
        from gui.sas_verify_app import run_sas_verify_app  # noqa: E402

        raise SystemExit(run_sas_verify_app([a for a in sys.argv[1:] if a != "--sas-verify"]))
    raise SystemExit(run_app())