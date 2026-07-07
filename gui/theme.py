"""
Dark palette (VS Code / Datadog–inspired) and global Qt stylesheet.

Per-widget colors in Python code should prefer :mod:`gui.palette_adapt` and
``QPalette`` roles (``PlaceholderText``, ``Link``, etc.) so Light / System themes
stay readable. The constants below remain for this stylesheet and for painting
delegates / timelines that are not yet palette-driven.
"""

from __future__ import annotations

# Core palette (dark stylesheet + non-widget graphics)
COLOR_BG = "#1e1e1e"
COLOR_BG_ELEVATED = "#252526"
COLOR_BG_INPUT = "#3c3c3c"
COLOR_BORDER = "#3e3e42"
COLOR_TEXT = "#d4d4d4"
COLOR_TEXT_MUTED = "#858585"
COLOR_ACCENT = "#007acc"
COLOR_CRITICAL = "#f44747"
COLOR_MEDIUM = "#d19a66"
COLOR_LOW = "#6a9fb5"
COLOR_SUCCESS = "#89d185"

STYLESHEET = f"""
QWidget {{
    background-color: {COLOR_BG};
    color: {COLOR_TEXT};
    font-size: 13px;
    font-family: "Segoe UI", "San Francisco", system-ui, sans-serif;
}}
QMainWindow {{
    background-color: {COLOR_BG};
}}
QLineEdit, QComboBox {{
    background-color: {COLOR_BG_INPUT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    padding: 6px 10px;
    min-height: 22px;
    selection-background-color: {COLOR_ACCENT};
}}
QPushButton {{
    background-color: {COLOR_BG_ELEVATED};
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    padding: 6px 14px;
    min-height: 22px;
}}
QPushButton:hover {{
    background-color: #2d2d30;
    border-color: {COLOR_ACCENT};
}}
QPushButton:pressed {{
    background-color: #1b1b1c;
}}
QPushButton#primary {{
    background-color: {COLOR_ACCENT};
    color: #ffffff;
    border-color: {COLOR_ACCENT};
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background-color: #1c8adb;
}}
QTableView {{
    background-color: {COLOR_BG_ELEVATED};
    alternate-background-color: #2a2d2e;
    gridline-color: {COLOR_BORDER};
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    selection-background-color: rgba(0, 122, 204, 0.35);
    selection-color: {COLOR_TEXT};
}}
QTableView::item {{
    padding: 4px 8px;
}}
QHeaderView::section {{
    background-color: {COLOR_BG_INPUT};
    color: {COLOR_TEXT};
    padding: 6px 8px;
    border: none;
    border-right: 1px solid {COLOR_BORDER};
    border-bottom: 1px solid {COLOR_BORDER};
    font-weight: 600;
}}
QTextBrowser, QPlainTextEdit {{
    background-color: {COLOR_BG_ELEVATED};
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    padding: 8px;
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
}}
QSplitter::handle {{
    background-color: {COLOR_BORDER};
    width: 3px;
}}
QFrame#metricCard {{
    background-color: {COLOR_BG_ELEVATED};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 12px 16px;
}}
QFrame#metricCardCritical {{
    background-color: {COLOR_BG_ELEVATED};
    border: 2px solid {COLOR_CRITICAL};
    border-radius: 6px;
    padding: 12px 16px;
}}
QFrame#metricCardPulse {{
    background-color: {COLOR_BG_ELEVATED};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 12px 16px;
}}
QFrame#metricCardPulse[alert="true"] {{
    border: 2px solid {COLOR_CRITICAL};
}}
QFrame#knownIssuesPanel {{
    background-color: {COLOR_BG_ELEVATED};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 4px;
}}
QLabel#metricValue {{
    font-size: 22px;
    font-weight: 700;
}}
QLabel#metricLabel {{
    color: {COLOR_TEXT_MUTED};
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QProgressBar {{
    border: 1px solid {COLOR_BORDER};
    border-radius: 3px;
    text-align: center;
    height: 18px;
    background-color: {COLOR_BG_INPUT};
}}
QProgressBar::chunk {{
    background-color: {COLOR_ACCENT};
    border-radius: 2px;
}}
QStatusBar {{
    background-color: {COLOR_BG_ELEVATED};
    border-top: 1px solid {COLOR_BORDER};
}}
"""
