#!/usr/bin/env python3
"""
Launch SAS accounting / meters verification as a standalone app.

Run from the project directory:

    python sas_verify_app.py
    python sas_verify_app.py --ip 10.0.0.90
    python sas_verify_app.py --scan-root \\\\10.0.0.90\\c$\\Goldclub\\var\\log
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import gui.app_logging  # noqa: F401
from gui.sas_verify_app import run_sas_verify_app  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(run_sas_verify_app())
