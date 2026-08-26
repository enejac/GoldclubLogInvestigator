"""SAS Verify cabinet IP control and unreachable-host fast-fail."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from gui.sas_verify_dialog import (
    CompareWorker,
    SasVerifyDialog,
    cabinet_unreachable_status,
    load_persisted_cabinet_ip,
    save_persisted_cabinet_ip,
)
from network.lab_access import LAB_FLEET_IPS


def test_lab_fleet_includes_111() -> None:
    assert "10.0.0.111" in LAB_FLEET_IPS


def test_cabinet_unreachable_status() -> None:
    text = cabinet_unreachable_status("10.0.0.90")
    assert "10.0.0.90" in text
    assert "Cabinet IP" in text


def test_compare_worker_skips_unc_when_smb_dead(monkeypatch) -> None:
    emitted: list[object] = []
    worker = CompareWorker("10.0.0.90", r"\\10.0.0.90\c$\Goldclub\var")
    worker.finished.connect(emitted.append)
    monkeypatch.setattr(
        "network.scanner_utils.is_smb_alive",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "network.goldclub_paths.layout_requires_smb",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "network.goldclub_paths.resolve_goldclub_layout",
        lambda *_a, **_k: MagicMock(kind=MagicMock(value="unc")),
    )

    def _boom(*_a, **_k):
        raise AssertionError("load_machine_accounting_state_pure should not run")

    monkeypatch.setattr(
        "network.accounting_state_loader.load_machine_accounting_state_pure",
        _boom,
    )
    worker.run()
    assert emitted == [{}]


def test_cabinet_ip_combo_updates_scan_root(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"\\10.0.0.90\c$\Goldclub\var\log",
    )
    monkeypatch.setattr(dlg, "_begin_cabinet_compare", lambda **_k: None)
    monkeypatch.setattr(dlg, "_schedule_onehand_check", lambda: None)
    monkeypatch.setattr(dlg, "_abort_inflight_for_scan_target_change", lambda **_k: None)
    dlg._on_cabinet_ip_changed("10.0.0.111")
    assert "10.0.0.111" in dlg._scan_root_edit.text()
    assert dlg._cabinet_ip_combo.currentText().strip() == "10.0.0.111"
    dlg.deleteLater()
    app.processEvents()


def test_scan_target_change_aborts_inflight_workers() -> None:
    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"\\10.0.0.90\c$\Goldclub\var",
    )
    dlg._compare_job_id = 3
    dlg._active_compare_job_id = 3
    dlg._compare_job_roots[3] = r"\\10.0.0.90\c$\Goldclub\var"
    dlg._meter_fetch_job_id = 8
    dlg._active_meter_fetch_job_id = 8
    dlg._last_scan_root_unc_host = "10.0.0.90"
    dlg._abort_inflight_for_scan_target_change(reason="test")
    assert dlg._active_compare_job_id == 4
    assert dlg._active_meter_fetch_job_id == 9
    assert dlg._compare_job_roots == {}
    assert dlg._compare_starting is False
    dlg.deleteLater()
    app.processEvents()


def test_cabinet_ip_change_is_debounced() -> None:
    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"\\10.0.0.90\c$\Goldclub\var",
    )
    calls: list[str] = []
    dlg._on_cabinet_ip_changed = lambda ip: calls.append(ip)  # type: ignore[method-assign]
    dlg._schedule_cabinet_ip_change("10.0.0.100")
    dlg._schedule_cabinet_ip_change("10.0.0.111")
    assert calls == []
    dlg._cabinet_ip_debounce_timer.stop()
    dlg._apply_debounced_cabinet_ip_change()
    assert calls == ["10.0.0.111"]
    dlg.deleteLater()
    app.processEvents()


def test_persisted_cabinet_ip_roundtrip(monkeypatch) -> None:
    store: dict[str, object] = {}

    class _FakeSettings:
        def value(self, key, default="", type=str):  # noqa: A002
            return store.get(key, default)

        def setValue(self, key, value):
            store[key] = value

        def remove(self, key):
            store.pop(key, None)

        def sync(self):
            pass

    monkeypatch.setattr(
        "gui.sas_verify_dialog._sas_verify_settings",
        lambda: _FakeSettings(),
    )
    save_persisted_cabinet_ip("10.0.0.111")
    assert load_persisted_cabinet_ip() == "10.0.0.111"