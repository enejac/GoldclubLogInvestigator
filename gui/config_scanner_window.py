"""Standalone window for the Config SHA1 Scanner."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QStatusBar

from gui.app_branding import apply_window_branding, status_bar_brand_pixmap
from gui.app_logging import configure_app_logging, get_logger
from gui.config_scanner_tab import ConfigScannerTabWidget
from gui.theme_utils import apply_theme
from config_manager import SettingsManager

logger = get_logger(__name__)


class ConfigScannerWindow(QMainWindow):
    """Full-size host for ConfigScannerTabWidget (separate from the log triage tabs)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        logger.info("ConfigScannerWindow.__init__ start")
        self.setWindowTitle("Config SHA1 Scanner")
        self.setMinimumSize(980, 720)
        self.resize(1180, 860)
        apply_window_branding(self)

        try:
            self._scanner = ConfigScannerTabWidget(self)
            logger.info("ConfigScannerTabWidget created")
        except Exception:
            logger.exception("ConfigScannerTabWidget failed during init")
            raise
        self.setCentralWidget(self._scanner)

        sb = QStatusBar(self)
        self.setStatusBar(sb)

        brand = QLabel()
        brand.setPixmap(status_bar_brand_pixmap(size=18))
        sb.addWidget(brand)
        sb.addWidget(QLabel("Config SHA1 Scanner"))
        self._status = QLabel("Ready")
        sb.addWidget(self._status, stretch=1)

        app = QApplication.instance()
        if app is not None:
            apply_theme(app, SettingsManager.get_theme())
        logger.info("ConfigScannerWindow.__init__ done")

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        logger.info("ConfigScannerWindow showEvent visible=%s", self.isVisible())

    def show_status(self, message: str) -> None:
        self._status.setText(message)


def run_config_scanner_app() -> int:
    """Launch only the Config Scanner window (no log triage UI)."""
    log_path = configure_app_logging()
    logger.info("run_config_scanner_app starting log_file=%s", log_path)
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Log Investigator")
    app.setApplicationDisplayName("Config SHA1 Scanner")
    app.setQuitOnLastWindowClosed(True)
    app.setStyle("Fusion")

    from gui.app_branding import apply_app_icon
    from gui.win_title_bar import install_title_bar_theme_filter, schedule_title_bar_theme

    apply_app_icon(app)
    install_title_bar_theme_filter(app, SettingsManager.get_theme)
    apply_theme(app, SettingsManager.get_theme())

    win = ConfigScannerWindow()
    win.show()
    schedule_title_bar_theme(win, SettingsManager.get_theme())
    return app.exec()