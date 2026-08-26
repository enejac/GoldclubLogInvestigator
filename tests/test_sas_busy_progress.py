"""Busy progress bar removed from SAS Verify; helpers/status checks remain."""

from gui.sas_verify_dialog import busy_progress_should_run, should_skip_cabinet_reload


def test_busy_progress_idle_by_default() -> None:
    assert busy_progress_should_run() is False


def test_busy_progress_runs_for_each_in_flight_kind() -> None:
    assert busy_progress_should_run(meters_ui_pending=True) is True
    assert busy_progress_should_run(compare_ui_pending=True) is True
    assert busy_progress_should_run(compare_running=True) is True
    assert busy_progress_should_run(meter_fetch_running=True) is True
    assert busy_progress_should_run(local_diff_running=True) is True


def test_busy_progress_has_no_onehand_clause() -> None:
    import inspect

    params = inspect.signature(busy_progress_should_run).parameters
    assert "onehand_check_running" not in params


def test_dialog_has_no_busy_progress_widget() -> None:
    from types import SimpleNamespace

    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"C:\Goldclub\var\log",
    )
    dlg._prefetch_started = True
    assert dlg._busy_progress is None
    dlg._set_busy_progress_active(True)
    dlg._suppress_busy_progress()
    dlg._apply_busy_progress_idle()
    dlg.deleteLater()
    app.processEvents()


def test_machine_loaded_check_ignores_ttl() -> None:
    common = dict(
        scan_root=r"\\10.0.0.90\c$\Goldclub\var\log",
        loaded_scan_root=r"\\10.0.0.90\c$\Goldclub\var\log",
        machine_state_loaded=True,
    )
    assert (
        should_skip_cabinet_reload(
            **common, loaded_at=0.0, ttl_seconds=15.0, now=1000.0
        )
        is False
    )
    assert should_skip_cabinet_reload(**common, ttl_seconds=None) is True


def test_machine_loaded_check_requires_matching_root() -> None:
    assert (
        should_skip_cabinet_reload(
            scan_root=r"\\10.0.0.90\c$\Goldclub\var\log",
            loaded_scan_root=r"\\10.0.0.171\c$\Goldclub\var\log",
            machine_state_loaded=True,
            ttl_seconds=None,
        )
        is False
    )
