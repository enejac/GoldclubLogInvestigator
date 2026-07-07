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

from gui.main_window import run_app  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(run_app())
