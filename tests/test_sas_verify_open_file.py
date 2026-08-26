"""Right-click "Open source file" for the SAS / Machine accounting columns.

Covers the file resolvers behind the context-menu actions added to
``SasVerifyDialog`` — they must point at the real backing file when one
exists (gm2au for Machine, SASControler* for a local-scan SAS side) and fall
back sanely (diagnostics log, or "no file") otherwise.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from gui.sas_verify_dialog import _newest_existing_file


def _mock_layout(state, *, log_root=None, goldclub_root=None):
    """Minimal layout stub for open-file resolvers (must include layout attrs)."""
    from network.goldclub_paths import GoldclubLayoutKind

    return SimpleNamespace(
        state_gcmessenger=state,
        themes_root=None,
        log_root=log_root,
        goldclub_root=goldclub_root,
        kind=GoldclubLayoutKind.CUSTOM,
    )


def _dialog(tmp_path, monkeypatch, *, scan_root: str = ""):
    from PySide6.QtCore import QThreadPool

    from gui.sas_verify_dialog import SasVerifyDialog

    vm = SimpleNamespace(current_product_name="GUI-Test")
    dlg = SasVerifyDialog(vm, QThreadPool.globalInstance(), scan_root=scan_root)
    monkeypatch.setattr(dlg, "_scan_root_text", lambda: scan_root)
    return dlg


def test_newest_existing_file_picks_most_recent(tmp_path) -> None:
    older = tmp_path / "older.xml"
    newer = tmp_path / "newer.xml"
    older.write_text("a", encoding="utf-8")
    time.sleep(0.02)
    newer.write_text("b", encoding="utf-8")

    assert _newest_existing_file([older, newer]) == newer
    assert _newest_existing_file([tmp_path / "missing.xml"]) is None
    assert _newest_existing_file([]) is None


def test_machine_source_file_finds_gm2au_snapshot(tmp_path, monkeypatch, qapp) -> None:
    import network.goldclub_paths as gp

    state = tmp_path / "state"
    (state / "gm2au").mkdir(parents=True)
    (state / "SASControler1").mkdir(parents=True)
    gm2au_file = state / "gm2au" / "DeviceManagerData.xml_1"
    gm2au_file.write_text("<root/>", encoding="utf-8")
    (state / "SASControler1" / "DeviceManagerData.xml_1").write_text(
        "<root/>", encoding="utf-8"
    )
    monkeypatch.setattr(
        gp,
        "resolve_goldclub_layout",
        lambda root: _mock_layout(state, log_root=tmp_path),
    )

    dlg = _dialog(tmp_path, monkeypatch, scan_root=str(tmp_path))
    assert dlg._machine_source_file() == gm2au_file
    dlg.deleteLater()
    qapp.processEvents()


def test_machine_source_file_none_without_scan_root(tmp_path, monkeypatch, qapp) -> None:
    dlg = _dialog(tmp_path, monkeypatch, scan_root="")
    assert dlg._machine_source_file() is None
    dlg.deleteLater()
    qapp.processEvents()


def test_sas_source_file_finds_sascontroler_snapshot_on_local_scan(
    tmp_path, monkeypatch, qapp
) -> None:
    import network.goldclub_paths as gp

    state = tmp_path / "state"
    (state / "SASControler1").mkdir(parents=True)
    sas_file = state / "SASControler1" / "DeviceManagerData.xml_1"
    sas_file.write_text("<root/>", encoding="utf-8")
    monkeypatch.setattr(
        gp,
        "resolve_goldclub_layout",
        lambda root: _mock_layout(state, log_root=tmp_path),
    )

    dlg = _dialog(tmp_path, monkeypatch, scan_root=str(tmp_path))
    assert dlg._sas_source_file() == sas_file
    dlg.deleteLater()
    qapp.processEvents()


def test_sas_source_file_falls_back_to_diagnostics_log(tmp_path, monkeypatch, qapp) -> None:
    import gui.app_logging as app_logging

    log_path = tmp_path / "SasVerifyMeters.log"
    log_path.write_text("diagnostics", encoding="utf-8")
    monkeypatch.setattr(
        app_logging,
        "log_file_path",
        lambda filename=None: log_path if filename == "SasVerifyMeters.log" else tmp_path / "missing.log",
    )

    dlg = _dialog(tmp_path, monkeypatch, scan_root="")
    assert dlg._sas_source_file() == log_path
    dlg.deleteLater()
    qapp.processEvents()


def test_sas_source_file_none_when_nothing_exists(tmp_path, monkeypatch, qapp) -> None:
    import gui.app_logging as app_logging

    monkeypatch.setattr(
        app_logging, "log_file_path", lambda filename=None: tmp_path / "missing.log"
    )

    dlg = _dialog(tmp_path, monkeypatch, scan_root="")
    assert dlg._sas_source_file() is None
    dlg.deleteLater()
    qapp.processEvents()


def test_open_source_file_reports_missing_file(qapp, monkeypatch, tmp_path) -> None:
    from PySide6.QtCore import QThreadPool

    from gui.sas_verify_dialog import SasVerifyDialog

    vm = SimpleNamespace(current_product_name="GUI-Test")
    dlg = SasVerifyDialog(vm, QThreadPool.globalInstance(), scan_root="")

    shown: list[str] = []
    monkeypatch.setattr(
        "gui.sas_verify_dialog.QMessageBox.information",
        lambda *a, **k: shown.append("info"),
    )
    dlg._open_source_file(None)
    assert shown == ["info"]

    dlg.deleteLater()
    qapp.processEvents()


def test_open_source_file_opens_existing_file(qapp, monkeypatch, tmp_path) -> None:
    from PySide6.QtCore import QThreadPool

    from gui.sas_verify_dialog import SasVerifyDialog

    vm = SimpleNamespace(current_product_name="GUI-Test")
    dlg = SasVerifyDialog(vm, QThreadPool.globalInstance(), scan_root="")

    target = tmp_path / "found.xml"
    target.write_text("<root/>", encoding="utf-8")

    import gui.notepad_pp as npp

    monkeypatch.setattr(npp, "open_with_notepad_or_npp", lambda p: (True, "opened"))
    warned: list[str] = []
    monkeypatch.setattr(
        "gui.sas_verify_dialog.QMessageBox.warning",
        lambda *a, **k: warned.append("warn"),
    )
    dlg._open_source_file(target)
    assert warned == []

    dlg.deleteLater()
    qapp.processEvents()


def test_machine_source_file_resolves_roulette_ruleta_var(tmp_path, monkeypatch, qapp) -> None:
    """Right-click Open Machine must find gm2au under ruleta\\var (not only GCMessenger)."""
    goldclub = tmp_path / "Goldclub"
    log_root = goldclub / "var" / "log" / "ruleta"
    log_root.mkdir(parents=True)
    gm2au = goldclub / "ruleta" / "var" / "gm2au"
    gm2au.mkdir(parents=True)
    gm2au_file = gm2au / "DeviceManagerData.xml_1"
    gm2au_file.write_text("<root/>", encoding="utf-8")
    (goldclub / "ruleta" / "var" / "SASControler1").mkdir(parents=True)
    (goldclub / "ruleta" / "var" / "SASControler1" / "DeviceManagerData.xml_1").write_text(
        "<root/>", encoding="utf-8"
    )

    dlg = _dialog(tmp_path, monkeypatch, scan_root=str(log_root))
    assert dlg._machine_source_file() == gm2au_file
    assert dlg._sas_source_file() == (
        goldclub / "ruleta" / "var" / "SASControler1" / "DeviceManagerData.xml_1"
    )
    dlg.deleteLater()
    qapp.processEvents()


def test_machine_source_file_resolves_bare_ruleta_var_scan_root(
    tmp_path, monkeypatch, qapp
) -> None:
    """Pasting …\\ruleta\\var (no var\\log) must still open the Machine snapshot."""
    goldclub = tmp_path / "Goldclub"
    ruleta_var = goldclub / "ruleta" / "var"
    gm2au = ruleta_var / "gm2au"
    gm2au.mkdir(parents=True)
    gm2au_file = gm2au / "DeviceManagerData.xml_1"
    gm2au_file.write_text("<root/>", encoding="utf-8")

    dlg = _dialog(tmp_path, monkeypatch, scan_root=str(ruleta_var))
    assert dlg._machine_source_file() == gm2au_file
    dlg.deleteLater()
    qapp.processEvents()


@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
