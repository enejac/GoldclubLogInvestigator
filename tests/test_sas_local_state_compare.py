"""Local scan root (G:\\ / on-EGM): SAS column from the SASControler1 snapshot.

Slot only — a local root has no host cable to poll, so the cabinet's own SAS
controller folder is the SAS side and gm2au stays the Machine side.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from gui.sas_verify_dialog import (
    SAS_STATUS_NOT_REPORTED,
    SAS_STATUS_PENDING,
    missing_sas_status,
)
from network.accounting_state_loader import (
    load_sas_controller_state,
    pick_gm2au_source,
    pick_sas_controller_source,
)

# 6F code -> normalized state key, mirroring the product alias table.
_ALIASES = {"0000": "coinin", "0001": "coinout"}


def _fake_vm() -> SimpleNamespace:
    return SimpleNamespace(
        current_product_name="GUI-Test",
        get_gm2u_value_for_sas_code=lambda code, state: state.get(
            _ALIASES.get((code or "").upper(), ""), ""
        ),
    )


def test_pick_sas_controller_source_finds_the_controller_folder() -> None:
    sources = {
        "gm2au": {"coinin": "10"},
        "SASControler1": {"coinin": "12"},
    }
    assert pick_sas_controller_source(sources) == {"coinin": "12"}
    assert pick_gm2au_source(sources) == {"coinin": "10"}
    assert pick_gm2au_source({"SASControler1": {"coinin": "12"}}) == {}


def test_pick_sas_controller_source_tolerates_naming_variants() -> None:
    assert pick_sas_controller_source({"sascontroller2": {"coinin": "7"}}) == {"coinin": "7"}
    assert pick_sas_controller_source({"SAS Controler": {"coinin": "8"}}) == {"coinin": "8"}


def test_pick_sas_controller_source_without_controller_folder() -> None:
    assert pick_sas_controller_source({"gm2au": {"coinin": "10"}}) == {}
    assert pick_sas_controller_source({}) == {}


def test_load_sas_controller_state_reads_only_the_controller_folder(
    tmp_path, monkeypatch
) -> None:
    import network.goldclub_paths as gp

    state = tmp_path / "state"
    (state / "SASControler1").mkdir(parents=True)
    (state / "gm2au").mkdir(parents=True)
    (state / "SASControler1" / "DeviceManagerData.xml_1").write_text(
        '<?xml version="1.0"?><root><meter meterName="coinIn" meterValue="1234"/></root>',
        encoding="utf-8",
    )
    (state / "gm2au" / "DeviceManagerData.xml_2").write_text(
        '<?xml version="1.0"?><root><meter meterName="coinIn" meterValue="9999"/></root>',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gp, "resolve_goldclub_layout", lambda root: SimpleNamespace(state_gcmessenger=state)
    )

    assert load_sas_controller_state(str(tmp_path))["coinin"] == "1234"


def _local_slot_dialog(kind: str):
    """Dialog wired to a local scan root with machine state already loaded."""
    from PySide6.QtCore import QThreadPool

    from gui.sas_verify_dialog import SasVerifyDialog

    root = r"G:\Goldclub\var"
    dlg = SasVerifyDialog(_fake_vm(), QThreadPool.globalInstance(), scan_root=root)
    dlg._prefetch_started = True
    dlg._local_files_only_mode = lambda: True  # type: ignore[method-assign]
    dlg._resolved_game_client_kind = lambda: kind  # type: ignore[method-assign]
    blocked = dlg._scan_root_edit.blockSignals(True)
    dlg._scan_root_edit.setText(root)
    dlg._scan_root_edit.blockSignals(blocked)
    dlg._scan_root = root
    dlg._machine_state = {"coinin": "1000", "coinout": "50"}
    dlg._machine_state_loaded = True
    dlg._loaded_cabinet_scan_root = root
    dlg._machine_state_loaded_at = time.monotonic()
    return dlg


def test_local_slot_fills_sas_column_from_sas_controller_snapshot() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _local_slot_dialog("slot")

    dlg._on_local_diff_done(
        "Local state folders agree (SASControler1 vs gm2au).",
        {"coinin": "1234", "coinout": "77"},
    )

    # SAS side is the controller snapshot; Machine side stays gm2au.
    assert dlg._sas_value_for_code("0000") == "1234"
    assert dlg._sas_value_for_code("0001") == "77"
    assert dlg._machine_value_for_code("0000", allow_machine_lookup=True) == "1000"
    # The label elides the middle; the full line lives on the tooltip / cache.
    status = dlg._prefetch_status_full or dlg._prefetch_status_label.toolTip()
    assert "SASControler1" in status
    assert "No COM capture" in status

    dlg.deleteLater()
    app.processEvents()


def test_local_roulette_keeps_sas_column_empty() -> None:
    """Roulette state folders carry no SAS controller meters — no fake SAS side."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _local_slot_dialog("roulette")

    dlg._on_local_diff_done("Local state folders agree.", {"coinin": "1234"})

    assert dlg._local_sas_state == {}
    assert dlg._sas_value_for_code("0000") == ""
    assert "SASControler1" not in dlg._prefetch_status_label.text()

    dlg.deleteLater()
    app.processEvents()


def test_missing_sas_status_wording() -> None:
    assert missing_sas_status(sas_capture_expected=True) == SAS_STATUS_PENDING
    assert missing_sas_status(sas_capture_expected=False) == SAS_STATUS_NOT_REPORTED


def test_local_root_reports_meters_the_snapshot_lacks_as_not_reported() -> None:
    """A snapshot has no wire to poll: unlisted meters are not 'pending'."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _local_slot_dialog("slot")

    assert dlg._sas_capture_expected() is False

    dlg._on_local_diff_done("", {"coinin": "1000"})
    # 0002 (jackpot) is absent from the cabinet's DeviceManager state.
    statuses = _statuses_by_code(dlg)
    assert statuses["0000"] == "MATCH"
    assert statuses["0002"] == SAS_STATUS_NOT_REPORTED

    dlg.deleteLater()
    app.processEvents()


def test_remote_root_keeps_pending_until_a_capture_completes() -> None:
    """With a COM capture still possible, an empty SAS cell is genuinely pending."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        _fake_vm(), QThreadPool.globalInstance(), scan_root=r"\\10.0.0.90\c$\Goldclub\var"
    )
    dlg._prefetch_started = True
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]

    assert dlg._sas_capture_expected() is True
    # Finished capture (even with paste) — unlisted / length-0 meters are not pending.
    dlg._cached_meter_result = SimpleNamespace(paste_text="RX<= 01 6F 05 00 00 AA BB")
    assert dlg._sas_capture_expected() is False
    dlg._cached_meter_result = SimpleNamespace(paste_text="")
    assert dlg._sas_capture_expected() is False

    dlg.deleteLater()
    app.processEvents()


def test_remote_capture_length0_meter_is_not_reported() -> None:
    """IGT Accounting RX with 6E00 length-0 must not fake MATCH $0 on .90."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import (
        SAS_STATUS_NOT_REPORTED,
        SasVerifyDialog,
        build_verify_6f_rows_from_paste,
    )

    # Batch-2 style frame: bills-in then length-0 6E then handpay.
    paste = (
        "TX>= 01 6F 10 00 00 0B 00 17 00 15 00 6E 00 23 00 16 00 18 00 FF B5\n"
        "RX<= 01 6F 4D 00 00 0B 00 09 00 00 00 00 00 00 10 72 00 17 00 09 00 00 "
        "00 00 00 00 00 00 00 15 00 09 00 00 00 00 00 00 00 00 00 6E 00 00 23 00 "
        "09 00 00 00 00 00 00 40 03 16 16 00 09 00 00 00 00 00 00 00 00 00 18 00 "
        "09 00 00 00 00 00 00 00 00 00 7E E9\n"
    )
    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        _fake_vm(), QThreadPool.globalInstance(), scan_root=r"\\10.0.0.90\c$\Goldclub\var"
    )
    dlg._prefetch_started = True
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    dlg._machine_state = {"coinin": "1"}
    dlg._machine_state_loaded = True
    dlg._cached_meter_result = SimpleNamespace(paste_text=paste, ok=True)

    rows = build_verify_6f_rows_from_paste(paste)
    by_id = {r.meter_id: r.sas_value_text for r in rows}
    assert by_id.get("000B")  # parsed
    assert by_id.get("006E", "") == ""  # length-0 → no value
    assert by_id.get("0023") == "400316"

    dlg._render(parsed_rows=rows, allow_machine_lookup=True)
    app.processEvents()
    statuses = _statuses_by_code(dlg)
    assert statuses["006E"] == SAS_STATUS_NOT_REPORTED

    dlg.deleteLater()
    app.processEvents()


def _statuses_by_code(dlg) -> dict[str, str]:
    from gui.sas_verify_dialog import COL_6F_CODE, COL_STATUS

    table = dlg._table
    out: dict[str, str] = {}
    for row in range(table.rowCount()):
        code = table.item(row, COL_6F_CODE)
        status = table.item(row, COL_STATUS)
        if code is not None and status is not None:
            out[code.text().strip().upper()] = status.text().strip()
    return out


def test_local_slot_ignores_an_empty_snapshot() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _local_slot_dialog("slot")

    assert dlg._apply_local_sas_source({}) is False
    assert dlg._sas_value_for_code("0000") == ""

    dlg.deleteLater()
    app.processEvents()


def test_remote_root_ignores_stale_local_sas_state() -> None:
    """COM/UNC mode must not paint SAS from a leftover SASControler snapshot."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import Sas6FRow, SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        _fake_vm(), QThreadPool.globalInstance(), scan_root=r"\\10.0.0.90\c$\Goldclub\var"
    )
    dlg._prefetch_started = True
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]
    dlg._cabinet_share_only_mode = False
    dlg._local_sas_state = {"coinin": "4242"}
    dlg._last_parsed_rows = [Sas6FRow(meter_id="0000", sas_value_text="")]

    assert dlg._share_meters_without_com() is False
    assert dlg._sas_value_for_code("0000") == ""

    dlg.deleteLater()
    app.processEvents()
