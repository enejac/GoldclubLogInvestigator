"""RAM Clear enablement must not require _scan_root_edit during menu construction."""

from types import SimpleNamespace

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from gui.sas_verify_dialog import SasVerifyDialog


def test_dialog_init_with_unc_scan_root_before_edit_exists() -> None:
    """Regression: menu bar called _cabinet_ip_from_scan_root before QLineEdit."""
    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"\\10.0.0.90\c$\Goldclub\var\log",
    )
    assert dlg._scan_root_edit is not None
    assert dlg._cabinet_ip_from_scan_root() == "10.0.0.90"
    dlg.deleteLater()
    app.processEvents()


def test_cabinet_ip_uses_scan_root_text_fallback() -> None:
    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"\\10.0.0.171\c$\Goldclub\var\log",
    )
    # Simulate pre-edit state
    edit = dlg._scan_root_edit
    del dlg._scan_root_edit
    assert dlg._cabinet_ip_from_scan_root() == "10.0.0.171"
    dlg._scan_root_edit = edit
    dlg.deleteLater()
    app.processEvents()