"""App-wide feedback when a disabled button is clicked.

Qt does not deliver events to a disabled widget, so a user clicking a greyed-out
button normally gets *no* feedback at all. ``DisabledClickWarner`` is a single
application-level event filter that detects a press landing on any disabled
``QAbstractButton`` (in the main window or any dialog) and shows a short, non-modal
warning explaining why it is unavailable.

The reason is resolved in this order:
1. the button's ``disabledReason`` dynamic property (set where a specific hint helps),
2. the button's tooltip,
3. a generic fallback using the button's label.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import QAbstractButton, QApplication, QToolTip, QWidget

# Don't climb the whole ancestor chain — a button's clickable area is the button
# itself or a thin wrapper (icon/label child).
_MAX_PARENT_DEPTH = 4


class DisabledClickWarner(QObject):
    """Application event filter that warns when a disabled button is clicked."""

    def __init__(
        self,
        parent: QObject | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._status_callback = status_callback

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.MouseButtonPress:
            try:
                self._maybe_warn(event)
            except Exception:  # noqa: BLE001 — feedback must never break input handling
                pass
        # Never consume: normal event processing continues unaffected.
        return False

    def _maybe_warn(self, event: QEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        global_pos = self._global_pos(event)
        if global_pos is None:
            return
        widget = QApplication.widgetAt(global_pos)
        button = self._disabled_button_at(widget)
        if button is None:
            return
        reason = self._reason_for(button)
        # A tooltip at the cursor reads as a contextual warning and is non-blocking.
        QToolTip.showText(global_pos, reason, button)
        if self._status_callback is not None:
            flat = " ".join(reason.split())
            self._status_callback(flat)

    @staticmethod
    def _global_pos(event: QEvent) -> QPoint | None:
        gp = getattr(event, "globalPosition", None)
        if callable(gp):
            return gp().toPoint()
        legacy = getattr(event, "globalPos", None)
        if callable(legacy):
            return legacy()
        return None

    @staticmethod
    def _disabled_button_at(widget: QWidget | None) -> QAbstractButton | None:
        cur: QWidget | None = widget
        depth = 0
        while cur is not None and depth < _MAX_PARENT_DEPTH:
            if isinstance(cur, QAbstractButton):
                # Enabled buttons handle their own clicks — nothing to warn about.
                return None if cur.isEnabled() else cur
            cur = cur.parentWidget()
            depth += 1
        return None

    @staticmethod
    def _reason_for(button: QAbstractButton) -> str:
        explicit = button.property("disabledReason")
        if explicit:
            return str(explicit)
        label = (button.text() or "").replace("&", "").strip()
        head = f"“{label}” is currently unavailable." if label else "This action is currently unavailable."
        tip = (button.toolTip() or "").strip()
        return f"{head}\n{tip}" if tip else head


def install_disabled_click_warner(
    status_callback: Callable[[str], None] | None = None,
) -> DisabledClickWarner | None:
    """Install the warner on the running ``QApplication``. Returns it (keep a reference)."""
    app = QApplication.instance()
    if app is None:
        return None
    warner = DisabledClickWarner(app, status_callback)
    app.installEventFilter(warner)
    return warner
