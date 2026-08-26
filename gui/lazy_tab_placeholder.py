"""Placeholder widgets for deferred QTabWidget pages."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

LAZY_MAIN_TAB_SPECS: tuple[tuple[str, str], ...] = (
    ("analytics", "Game Analytics"),
    ("automation", "Automated Tests"),
    ("software_version", "Software Version"),
    ("history", "History"),
    ("bug_detector", "Bug Detector"),
    ("fleet", "Fleet Overview"),
)

_keys = [k for k, _ in LAZY_MAIN_TAB_SPECS]
if len(set(_keys)) != len(_keys):
    raise RuntimeError(f"duplicate lazy tab keys in LAZY_MAIN_TAB_SPECS: {_keys}")


def lazy_tab_placeholder(title: str) -> QWidget:
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(24, 24, 24, 24)
    label = QLabel(f"Loading {title}…")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet("color: #888;")
    layout.addStretch(1)
    layout.addWidget(label)
    layout.addStretch(1)
    return host