"""Always-visible thin busy progress line (no show/hide layout flicker)."""

from __future__ import annotations

from PySide6.QtWidgets import QProgressBar, QSizePolicy, QWidget

# Keep idle and busy at the same reserved height — Windows styles sometimes
# ignore stylesheet min/max-height on an indeterminate bar, so FixedHeight wins.
THIN_BUSY_HEIGHT_PX = 3

_THIN_BUSY_STYLE = f"""
QProgressBar {{
    border: none;
    background: #dde3ea;
    border-radius: 1px;
    max-height: {THIN_BUSY_HEIGHT_PX}px;
    min-height: {THIN_BUSY_HEIGHT_PX}px;
}}
QProgressBar::chunk {{
    background-color: #2f7dbf;
    border-radius: 1px;
    margin: 0px;
}}
"""


def make_thin_busy_progress(parent: QWidget | None = None) -> QProgressBar:
    """Hairline indeterminate bar that keeps its layout slot when idle."""
    bar = QProgressBar(parent)
    bar.setTextVisible(False)
    bar.setFixedHeight(THIN_BUSY_HEIGHT_PX)
    bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    bar.setStyleSheet(_THIN_BUSY_STYLE)
    set_thin_busy_progress_active(bar, False)
    return bar


def set_thin_busy_progress_active(bar: QProgressBar, active: bool) -> None:
    """Toggle indeterminate animation without resetting an already-busy bar.

    Re-applying ``setRange(0, 0)`` while busy restarts the chunk animation and
    makes the line look stuttery when callers refresh busy state frequently.
    """
    # Re-assert the reserved height every toggle — some styles grow the bar
    # when it becomes indeterminate.
    bar.setFixedHeight(THIN_BUSY_HEIGHT_PX)
    want = bool(active)
    is_busy = bar.maximum() == 0  # Qt indeterminate convention
    if want:
        if is_busy:
            return
        bar.setRange(0, 0)
        return
    # Idle: normalize to a quiescent 0..1 bar (default QProgressBar is 0..100).
    if not is_busy and bar.maximum() == 1 and bar.value() == 0:
        return
    bar.setRange(0, 1)
    bar.setValue(0)
