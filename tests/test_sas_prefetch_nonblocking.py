"""Prefetch must not block the UI thread after remote Machine XML lands."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from gui.sas_verify_dialog import SasVerifyDialog


def _clear_auto_fetch_pref() -> None:
    from config_manager import SettingsManager
    from gui.sas_verify_dialog import _KEY_AUTO_FETCH

    s = SettingsManager._s()
    s.remove(f"sasVerify/{_KEY_AUTO_FETCH}")
    s.sync()


def _dlg(scan_root: str = r"\\10.0.0.90\c$\Goldclub\var\log") -> SasVerifyDialog:
    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=scan_root,
    )
    dlg._prefetch_started = True
    return dlg


def test_continue_prefetch_uses_cached_blockers_never_force(monkeypatch) -> None:
    _clear_auto_fetch_pref()
    dlg = _dlg()
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._sas_mux_accessible = lambda **_k: ("COM4", "COM4 selected")  # type: ignore[method-assign]
    dlg._cabinet_ip_from_scan_root = lambda: "10.0.0.90"  # type: ignore[method-assign]
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    dlg._cabinet_share_meters_mode = lambda: False  # type: ignore[method-assign]
    calls: list[dict] = []
    fetch_calls: list[str] = []

    def _fake_blockers(**kwargs):
        calls.append(dict(kwargs))
        return []

    monkeypatch.setattr(
        "network.sas_serial_meters.find_running_sas_com_blockers",
        _fake_blockers,
    )
    dlg._begin_meter_fetch = lambda **kw: fetch_calls.append("meters") or True  # type: ignore[method-assign]
    dlg._continue_prefetch_com_tier()
    assert fetch_calls == ["meters"]
    assert calls
    assert all(c.get("cached_only") is True for c in calls)
    assert all(not c.get("force_refresh") for c in calls)
    dlg.deleteLater()
    QApplication.instance().processEvents()
    _clear_auto_fetch_pref()


def test_auto_fetch_off_flushes_pending_machine_paint(monkeypatch) -> None:
    _clear_auto_fetch_pref()
    dlg = _dlg()
    dlg._auto_fetch_toggle.setChecked(False)
    dlg._sas_mux_accessible = lambda **_k: ("COM4", "COM4 selected")  # type: ignore[method-assign]
    dlg._cabinet_ip_from_scan_root = lambda: "10.0.0.90"  # type: ignore[method-assign]
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    dlg._cabinet_share_meters_mode = lambda: False  # type: ignore[method-assign]
    flushed: list[str] = []
    dlg._flush_pending_cabinet_ui_refresh = (  # type: ignore[method-assign]
        lambda: flushed.append("flush")
    )
    monkeypatch.setattr(
        "network.sas_serial_meters.find_running_sas_com_blockers",
        lambda **kw: [],
    )
    dlg._continue_prefetch_com_tier()
    assert flushed == ["flush"]
    dlg.deleteLater()
    QApplication.instance().processEvents()
    _clear_auto_fetch_pref()


def test_start_prefetch_starts_compare_before_process_events(monkeypatch) -> None:
    """Busy paint must not re-enter before the Machine compare worker is marked running."""
    _clear_auto_fetch_pref()
    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"\\10.0.0.90\c$\Goldclub\var\log",
    )
    order: list[str] = []

    def _compare(**_kw):
        order.append("compare")

    def _paint():
        order.append("paint")

    dlg._begin_cabinet_compare = _compare  # type: ignore[method-assign]
    dlg._paint_immediate_busy_feedback = _paint  # type: ignore[method-assign]
    dlg._cabinet_ip_from_scan_root = lambda: ""  # type: ignore[method-assign]
    monkeypatch.setattr(
        "gui.sas_verify_dialog.QTimer.singleShot",
        lambda *_a, **_k: None,
    )
    dlg._start_prefetch()
    assert order == ["compare", "paint"]
    dlg.deleteLater()
    app.processEvents()
    _clear_auto_fetch_pref()


def test_prefetch_compare_does_not_smb_remap(monkeypatch) -> None:
    """Opening against a cabinet UNC must not call resolve_sas_verify_scan_root on the UI thread."""
    _clear_auto_fetch_pref()
    dlg = _dlg()
    dlg._resolve_active_scan_root = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("remap")
    )
    dlg._compare_running = lambda: True  # type: ignore[method-assign]
    dlg._begin_cabinet_compare(prefetch=True)
    dlg.deleteLater()
    QApplication.instance().processEvents()
    _clear_auto_fetch_pref()


def test_begin_meter_fetch_prefetch_never_force_refreshes_blockers(monkeypatch) -> None:
    _clear_auto_fetch_pref()
    dlg = _dlg()
    calls: list[dict] = []

    def _fake_blockers(**kwargs):
        calls.append(dict(kwargs))
        return []

    monkeypatch.setattr(
        "network.sas_serial_meters.find_running_sas_com_blockers",
        _fake_blockers,
    )
    monkeypatch.setattr(
        "network.sas_serial_meters.sas_com_blocker_cache_ready",
        lambda: False,
    )
    dlg._resolved_com_port_for_fetch = lambda: "COM4"  # type: ignore[method-assign]
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    dlg._cabinet_share_meters_mode = lambda: False  # type: ignore[method-assign]

    # Abort before starting a real QThread — we only care about the blocker call.
    dlg._meter_fetch_running = lambda: True  # type: ignore[method-assign]
    assert dlg._begin_meter_fetch(prefetch=True, force=False) is False
    assert calls == [{"cached_only": True}]
    dlg.deleteLater()
    QApplication.instance().processEvents()
    _clear_auto_fetch_pref()


def test_enumerate_serial_ports_ttl_cache(monkeypatch) -> None:
    import network.sas_serial_meters as m

    calls = {"n": 0}

    class _P:
        device = "COM4"
        description = "USB Serial"
        hwid = "USB"

    def _comports():
        calls["n"] += 1
        return [_P()]

    monkeypatch.setattr(m, "_require_pyserial", lambda: object())
    monkeypatch.setattr(
        "serial.tools.list_ports.comports",
        _comports,
        raising=False,
    )
    # Direct patch of the import site used inside the function:
    import serial.tools.list_ports as list_ports

    monkeypatch.setattr(list_ports, "comports", _comports)
    m._ports_cache_valid = False
    m._ports_cache_value = []
    m._ports_cache_at = 0.0
    a = m.enumerate_serial_ports()
    b = m.enumerate_serial_ports()
    assert calls["n"] == 1
    assert [p.device for p in a] == [p.device for p in b]
    c = m.enumerate_serial_ports(force_refresh=True)
    assert calls["n"] == 2
    assert [p.device for p in c] == ["COM4"]
    m._ports_cache_valid = False
    m._ports_cache_value = []
    cached = m.enumerate_serial_ports(cached_only=True)
    assert cached == []
    assert calls["n"] == 2


def test_deferred_startup_warm_skips_ui_thread_com_enum(monkeypatch) -> None:
    """list_ports on the UI thread used to freeze SAS Verify at open."""
    _clear_auto_fetch_pref()
    dlg = _dlg()
    started: list[bool] = []

    def _boom(*_a, **_k):
        raise AssertionError("enumerate_serial_ports on UI thread")

    monkeypatch.setattr(
        "network.sas_serial_meters.enumerate_serial_ports",
        _boom,
    )

    def _bg():
        started.append(True)

    dlg._schedule_com_port_refresh_background = _bg  # type: ignore[method-assign]
    dlg._continue_prefetch_com_tier = lambda: None  # type: ignore[method-assign]
    dlg._sync_magicwheel_tab_visibility = lambda: None  # type: ignore[method-assign]
    dlg._deferred_startup_warm()
    assert started == [True]
    dlg.deleteLater()
    QApplication.instance().processEvents()
    _clear_auto_fetch_pref()