"""Auto fetch off must not start meter captures or queued refreshes."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QSettings, QThreadPool
from PySide6.QtWidgets import QApplication

from gui.sas_verify_dialog import (
    _KEY_AUTO_FETCH,
    _SAS_VERIFY_SETTINGS_GROUP,
    SasVerifyDialog,
)


def _clear_auto_fetch_pref() -> None:
    from config_manager import SettingsManager

    s = SettingsManager._s()
    s.remove(f"sasVerify/{_KEY_AUTO_FETCH}")
    s.sync()


def _dlg(scan_root: str = "") -> SasVerifyDialog:
    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=scan_root,
    )
    dlg._prefetch_started = True
    return dlg


def test_auto_fetch_pref_persists_off() -> None:
    _clear_auto_fetch_pref()
    app = QApplication.instance() or QApplication([])
    dlg = _dlg()
    assert dlg._auto_fetch_toggle.isChecked()
    dlg._auto_fetch_toggle.setChecked(False)
    dlg._save_auto_fetch_pref()
    dlg.deleteLater()
    app.processEvents()

    dlg2 = _dlg()
    assert dlg2._auto_fetch_toggle.isChecked() is False
    dlg2.deleteLater()
    app.processEvents()
    _clear_auto_fetch_pref()


def test_queue_and_run_respect_auto_fetch_off() -> None:
    _clear_auto_fetch_pref()
    dlg = _dlg()
    calls: list[str] = []
    dlg._begin_cabinet_compare = lambda **kw: calls.append("compare")  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: calls.append("meters") or True  # type: ignore[method-assign]

    dlg._auto_fetch_toggle.setChecked(False)
    dlg._queue_auto_fetch_refresh()
    assert dlg._auto_fetch_refresh_queued is False
    assert not dlg._auto_fetch_queue_timer.isActive()

    dlg._auto_fetch_refresh_queued = True
    dlg._run_queued_auto_fetch_refresh()
    assert calls == []
    assert dlg._auto_fetch_refresh_queued is False
    dlg.deleteLater()
    QApplication.instance().processEvents()
    _clear_auto_fetch_pref()


def test_prefetch_com_tier_skips_meters_when_auto_fetch_off(monkeypatch) -> None:
    _clear_auto_fetch_pref()
    dlg = _dlg(scan_root=r"\\10.0.0.90\c$\Goldclub\var\log")
    calls: list[str] = []
    dlg._auto_fetch_toggle.setChecked(False)
    dlg._sas_mux_accessible = lambda **_k: ("COM3", "")  # type: ignore[method-assign]
    dlg._cabinet_ip_from_scan_root = lambda: "10.0.0.90"  # type: ignore[method-assign]
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: calls.append("meters") or True  # type: ignore[method-assign]
    monkeypatch.setattr(
        "network.sas_serial_meters.find_running_sas_com_blockers",
        lambda **kw: [],
    )
    dlg._continue_prefetch_com_tier()
    assert calls == []
    status = dlg._prefetch_status_full or dlg._prefetch_status_label.text()
    assert "Auto fetch off" in status
    dlg.deleteLater()
    QApplication.instance().processEvents()
    _clear_auto_fetch_pref()


def test_prefetch_arms_com_recovery_when_igt_holds_port(monkeypatch) -> None:
    """Closing the SAS tester must trigger recapture even if Auto fetch was off at open."""
    _clear_auto_fetch_pref()
    dlg = _dlg(scan_root=r"\\10.0.0.90\c$\Goldclub\var\log")
    fetch_calls: list[str] = []
    dlg._auto_fetch_toggle.setChecked(False)
    dlg._sas_mux_accessible = lambda **_k: ("COM4", "COM4 selected")  # type: ignore[method-assign]
    dlg._cabinet_ip_from_scan_root = lambda: "10.0.0.90"  # type: ignore[method-assign]
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    dlg._cabinet_share_meters_mode = lambda: False  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: fetch_calls.append("meters") or True  # type: ignore[method-assign]
    monkeypatch.setattr(
        "network.sas_serial_meters.find_running_sas_com_blockers",
        lambda **kw: ["sastestx_cwh.exe"],
    )
    dlg._continue_prefetch_com_tier()
    assert fetch_calls == []
    assert dlg._com_recovery_pending
    assert dlg._recovery_timer.isActive()
    status = dlg._prefetch_status_full or dlg._prefetch_status_label.text()
    assert "retries automatically" in status
    dlg.deleteLater()
    QApplication.instance().processEvents()
    _clear_auto_fetch_pref()


def test_prefetch_with_auto_fetch_on_still_starts_capture_when_blockers(monkeypatch) -> None:
    _clear_auto_fetch_pref()
    dlg = _dlg(scan_root=r"\\10.0.0.90\c$\Goldclub\var\log")
    fetch_calls: list[str] = []
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._sas_mux_accessible = lambda **_k: ("COM4", "COM4 selected")  # type: ignore[method-assign]
    dlg._cabinet_ip_from_scan_root = lambda: "10.0.0.90"  # type: ignore[method-assign]
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    dlg._cabinet_share_meters_mode = lambda: False  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: fetch_calls.append("meters") or True  # type: ignore[method-assign]
    monkeypatch.setattr(
        "network.sas_serial_meters.find_running_sas_com_blockers",
        lambda **kw: ["sastestx_cwh.exe"],
    )
    dlg._continue_prefetch_com_tier()
    assert fetch_calls == ["meters"]
    assert dlg._com_recovery_pending
    dlg.deleteLater()
    QApplication.instance().processEvents()
    _clear_auto_fetch_pref()


def test_prefetch_port_busy_without_igt_is_access_denied(monkeypatch) -> None:
    """Prefetch must not offer share recovery or arm IGT watch when tester is down."""
    _clear_auto_fetch_pref()
    dlg = _dlg(scan_root=r"\\10.0.0.90\c$\Goldclub\var\log")
    prompts: list[dict] = []
    dlg._auto_fetch_toggle.setChecked(False)
    dlg._sas_mux_accessible = lambda **_k: (  # type: ignore[method-assign]
        "",
        "could not open port 'COM4': PermissionError(13, 'Access is denied', None, 5)",
    )
    dlg._cabinet_ip_from_scan_root = lambda: "10.0.0.90"  # type: ignore[method-assign]
    dlg._scan_root_unc_host = lambda: "10.0.0.90"  # type: ignore[method-assign]
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]
    dlg._offer_cabinet_share_when_com_blocked = lambda **kw: prompts.append(kw) or False  # type: ignore[method-assign]
    dlg._com_recovery_pending = True
    dlg._recovery_timer.start()
    monkeypatch.setattr(
        "gui.sas_verify_dialog.igt_sas_tester_is_running",
        lambda force_refresh=False: False,
    )
    dlg._continue_prefetch_com_tier()
    assert prompts == []
    assert not dlg._com_recovery_pending
    assert not dlg._recovery_timer.isActive()
    status = dlg._prefetch_status_full or dlg._prefetch_status_label.text()
    assert "No IGT SAS tester" in status
    dlg.deleteLater()
    QApplication.instance().processEvents()
    _clear_auto_fetch_pref()
    _clear_auto_fetch_pref()
    dlg = _dlg()
    dlg._begin_cabinet_compare = lambda **kw: None  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: True  # type: ignore[method-assign]
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._arm_default_auto_fetch()
    assert dlg._auto_fetch_timer.isActive()
    dlg._auto_fetch_toggle.setChecked(False)
    assert not dlg._auto_fetch_timer.isActive()
    assert dlg._auto_fetch_refresh_queued is False
    assert not dlg._auto_fetch_queue_timer.isActive()
    dlg.deleteLater()
    QApplication.instance().processEvents()
    _clear_auto_fetch_pref()