"""SAS Verify must paint chrome before any cabinet UNC / COM I/O."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from gui.sas_verify_dialog import SasVerifyDialog, TAB_MAGICWHEEL, _scan_root_is_unc


UNC = r"\\10.0.0.90\c$\Goldclub\var\log"


def test_scan_root_is_unc_helper() -> None:
    assert _scan_root_is_unc(UNC)
    assert _scan_root_is_unc("//10.0.0.90/c$/Goldclub/var")
    assert not _scan_root_is_unc(r"C:\Goldclub\var\log")
    assert not _scan_root_is_unc("")


def test_dialog_init_unc_does_not_probe_magic_wheel_or_layout(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    calls: list[str] = []

    def _boom_mw(*_a, **_k):
        calls.append("magic_wheel")
        return False

    def _boom_layout(*_a, **_k):
        calls.append("layout")
        raise AssertionError("resolve_goldclub_layout during UNC dialog init")

    monkeypatch.setattr(
        "network.magic_wheel_loader.is_magic_wheel_config_setup",
        _boom_mw,
    )
    monkeypatch.setattr(
        "network.goldclub_paths.resolve_goldclub_layout",
        _boom_layout,
    )
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=UNC,
    )
    dlg._prefetch_started = True
    assert calls == []
    assert not dlg._meter_tab_is_visible(TAB_MAGICWHEEL)
    dlg.deleteLater()
    app.processEvents()


def test_magic_wheel_unc_skips_slot_share(monkeypatch) -> None:
    from network.magic_wheel_loader import resolve_magic_wheel_config_path

    seen: list[str] = []

    def _fake_is_file(self: Path) -> bool:
        seen.append(str(self))
        return False

    monkeypatch.setattr(Path, "is_file", _fake_is_file)
    resolve_magic_wheel_config_path(UNC)
    blob = "\n".join(seen).replace("/", "\\").lower()
    assert r"\10.0.0.90\slot" not in blob
    assert "goldclub" in blob and "magicwheel_config.xml" in blob
