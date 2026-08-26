"""Standalone window for the offline AI Helper."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QStatusBar

from config_manager import SettingsManager
from gui.ai_helper_panel import AiHelperPanel
from gui.app_branding import apply_window_branding, status_bar_brand_pixmap
from gui.app_logging import get_logger
from gui.theme_utils import apply_theme

logger = get_logger(__name__)


class AiHelperWindow(QMainWindow):
    """Host for AiHelperPanel (separate from log triage)."""

    def __init__(
        self,
        parent=None,
        *,
        search_roots: list[str] | None = None,
        roots_notes: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        logger.info("AiHelperWindow.__init__ start")
        self.setWindowTitle("AI Helper (Offline)")
        self.setMinimumSize(720, 560)
        self.resize(900, 680)
        apply_window_branding(self)

        self._panel = AiHelperPanel(self)
        if search_roots is not None or roots_notes:
            self._panel.set_search_roots(
                list(search_roots or []),
                notes=list(roots_notes or []),
            )
        self.setCentralWidget(self._panel)

        sb = QStatusBar(self)
        self.setStatusBar(sb)
        brand = QLabel()
        brand.setPixmap(status_bar_brand_pixmap(size=18))
        sb.addWidget(brand)
        sb.addWidget(QLabel("AI Helper"))
        self._status = QLabel("Ready")
        sb.addWidget(self._status, stretch=1)

        self._panel.status_changed.connect(self.show_status)
        self._panel.refresh_model_status()

        app = QApplication.instance()
        if app is not None:
            apply_theme(app, SettingsManager.get_theme())
        logger.info("AiHelperWindow.__init__ done")

    def set_search_roots(
        self,
        roots: list[str],
        *,
        notes: list[str] | None = None,
    ) -> None:
        self._panel.set_search_roots(roots, notes=notes)

    def show_status(self, message: str) -> None:
        self._status.setText(message or "Ready")
