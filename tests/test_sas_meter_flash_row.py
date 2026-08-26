"""Whole-row meter flash after confirmed MATCH changes."""

from __future__ import annotations

import time
from types import SimpleNamespace

from PySide6.QtCore import Qt

from gui.sas_verify_dialog import (
    COL_6F_CODE,
    COL_COUNT,
    COL_MACHINE_VALUE,
    COL_METER_NAME,
    COL_SAS_6F_VALUE,
    COL_STATUS,
    SAS_STATUS_SYNCING,
)


_ALIASES = {"0000": "coinin", "0001": "coinout"}


def _fake_vm() -> SimpleNamespace:
    return SimpleNamespace(
        current_product_name="GUI-Test",
        get_gm2u_value_for_sas_code=lambda code, state: state.get(
            _ALIASES.get((code or "").upper(), ""), ""
        ),
    )


def _dialog():
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    root = r"G:\Goldclub\var"
    dlg = SasVerifyDialog(_fake_vm(), QThreadPool.globalInstance(), scan_root=root)
    dlg._prefetch_started = True
    dlg._local_files_only_mode = lambda: True  # type: ignore[method-assign]
    dlg._resolved_game_client_kind = lambda: "slot"  # type: ignore[method-assign]
    dlg._reload_game_theme_catalog = lambda: None  # type: ignore[method-assign]
    blocked = dlg._scan_root_edit.blockSignals(True)
    dlg._scan_root_edit.setText(root)
    dlg._scan_root_edit.blockSignals(blocked)
    dlg._scan_root = root
    dlg._machine_state = {"coinin": "1000", "coinout": "50"}
    dlg._machine_state_loaded = True
    dlg._loaded_cabinet_scan_root = root
    dlg._machine_state_loaded_at = time.monotonic()
    dlg._on_local_diff_done("", {"coinin": "1000", "coinout": "50"})
    return app, dlg


def _row_for_code(dlg, code: str) -> int:
    table = dlg._table
    for row in range(table.rowCount()):
        item = table.item(row, COL_6F_CODE)
        if item and item.text().strip().upper() == code:
            return row
    raise AssertionError(f"meter {code} not in table")


def _row_has_flash_fill(dlg, row: int) -> bool:
    table = dlg._table
    for col in range(COL_COUNT):
        item = table.item(row, col)
        if item is None:
            continue
        brush = item.background()
        if brush.style() == Qt.BrushStyle.NoBrush:
            continue
        if brush.color().alpha() > 0:
            return True
    return False


def test_only_a_real_numeric_increase_counts_as_a_meter_change() -> None:
    """Meters are monotonic counters; everything else is reload churn.

    A raw string compare flashed rows whose number never moved, because each
    Auto fetch reload blanks the Machine column and refills it with the same
    value.
    """
    from gui.sas_verify_dialog import meter_value_increased

    assert meter_value_increased("49580", "49680") is True
    assert meter_value_increased("0", "1") is True

    assert meter_value_increased("49580", "49580") is False
    # The blank-then-refill cycle of a reload, in both directions.
    assert meter_value_increased("", "49580") is False
    assert meter_value_increased("49580", "") is False
    # A counter never goes down; a smaller value means a different snapshot.
    assert meter_value_increased("49680", "49580") is False
    # Placeholders and junk are not numbers.
    assert meter_value_increased("—", "49580") is False
    assert meter_value_increased("49580", "PENDING") is False
    assert meter_value_increased(" 49580 ", " 49580 ") is False


def test_reload_that_refills_the_same_value_does_not_flash() -> None:
    """The reported bug: orange fade on a row whose number never increased."""
    app, dlg = _dialog()
    dlg._mark_meters_fetched()

    # A reload blanks the Machine column, then refills it with the same numbers.
    dlg._render(parsed_rows=dlg._last_parsed_rows, allow_machine_lookup=False)
    dlg._mark_meters_fetched()
    dlg._on_local_diff_done("", {"coinin": "1000", "coinout": "50"})

    assert not dlg._meter_flash_pending
    assert not dlg._meter_flash_keys
    assert not _row_has_flash_fill(dlg, _row_for_code(dlg, "0000"))

    # A genuine increment on the same row still flashes.
    dlg._mark_meters_fetched()
    dlg._machine_state = {"coinin": "1100", "coinout": "50"}
    dlg._on_local_diff_done("", {"coinin": "1100", "coinout": "50"})
    assert "0000" in dlg._meter_flash_keys
    assert _row_has_flash_fill(dlg, _row_for_code(dlg, "0000"))

    dlg.deleteLater()
    app.processEvents()


def test_flash_envelope_holds_full_then_fades_monotonically() -> None:
    """A highlight you can catch: lit for ~3 s, and never dark in the middle.

    The old envelope was two sine beats over 1.35 s, so it was already back to
    zero before you looked up from the wheel, and dipped to nothing halfway
    through even while it was running.
    """
    from gui.sas_verify_dialog import (
        METER_FLASH_DURATION_S,
        METER_FLASH_HOLD_S,
        meter_flash_strength,
    )

    assert METER_FLASH_DURATION_S >= 3.0
    assert 0.0 < METER_FLASH_HOLD_S < METER_FLASH_DURATION_S

    assert meter_flash_strength(0.0) == 1.0
    assert meter_flash_strength(METER_FLASH_HOLD_S) == 1.0
    # Outside the window there is no fill at all.
    assert meter_flash_strength(-0.1) == 0.0
    assert meter_flash_strength(METER_FLASH_DURATION_S) == 0.0

    # Strictly non-increasing after the hold, and still clearly visible at 2 s.
    fade = [
        meter_flash_strength(t / 100.0)
        for t in range(int(METER_FLASH_HOLD_S * 100), int(METER_FLASH_DURATION_S * 100))
    ]
    assert all(b <= a + 1e-9 for a, b in zip(fade, fade[1:]))
    assert meter_flash_strength(2.0) > 0.25
    assert meter_flash_strength(METER_FLASH_DURATION_S - 0.05) < 0.05


def test_flash_fill_is_visible_at_peak_and_gone_at_the_end() -> None:
    from gui.sas_verify_dialog import (
        METER_FLASH_PEAK_ALPHA,
        meter_flash_background,
    )

    peak = meter_flash_background(1.0)
    assert peak is not None
    assert peak.alpha() == METER_FLASH_PEAK_ALPHA
    # Weak enough to keep the cell text readable, strong enough to notice.
    assert 96 <= METER_FLASH_PEAK_ALPHA <= 190
    assert meter_flash_background(0.0) is None


def test_confirmed_match_change_pulses_entire_row() -> None:
    app, dlg = _dialog()
    row = _row_for_code(dlg, "0000")
    assert dlg._table.item(row, COL_STATUS).text() == "MATCH"

    dlg._mark_meters_fetched()
    dlg._machine_state = {"coinin": "1100", "coinout": "50"}
    dlg._on_local_diff_done("", {"coinin": "1100", "coinout": "50"})

    row = _row_for_code(dlg, "0000")
    assert dlg._table.item(row, COL_STATUS).text() == "MATCH"
    assert "0000" in dlg._meter_flash_keys
    assert _row_has_flash_fill(dlg, row)
    for col in (COL_METER_NAME, COL_SAS_6F_VALUE, COL_MACHINE_VALUE, COL_STATUS):
        item = dlg._table.item(row, col)
        assert item is not None
        brush = item.background()
        assert brush.style() != Qt.BrushStyle.NoBrush
        assert brush.color().alpha() > 0

    unchanged = _row_for_code(dlg, "0001")
    assert "0001" not in dlg._meter_flash_keys
    assert not _row_has_flash_fill(dlg, unchanged)

    dlg._stop_meter_flash()
    assert not dlg._meter_flash_keys
    assert not _row_has_flash_fill(dlg, row)

    dlg.deleteLater()
    app.processEvents()


def test_syncing_change_waits_for_match_before_row_pulse() -> None:
    app, dlg = _dialog()
    dlg._mark_meters_fetched()
    dlg._auto_fetch_round_active = True
    dlg._machine_state = {"coinin": "1200", "coinout": "50"}
    # Keep the round open (local_diff_done would otherwise end it).
    dlg._end_auto_fetch_round = lambda **_kw: None  # type: ignore[method-assign]
    dlg._on_local_diff_done("", {"coinin": "1000", "coinout": "50"})

    assert dlg._compare_sources_settled() is False
    assert "0000" in dlg._meter_flash_pending
    assert "0000" not in dlg._meter_flash_keys
    assert dlg._table.item(_row_for_code(dlg, "0000"), COL_STATUS).text() == SAS_STATUS_SYNCING
    assert not _row_has_flash_fill(dlg, _row_for_code(dlg, "0000"))

    dlg._auto_fetch_round_active = False
    dlg._mark_meters_fetched()
    dlg._on_local_diff_done("", {"coinin": "1200", "coinout": "50"})

    assert dlg._table.item(_row_for_code(dlg, "0000"), COL_STATUS).text() == "MATCH"
    assert "0000" in dlg._meter_flash_keys
    assert _row_has_flash_fill(dlg, _row_for_code(dlg, "0000"))

    dlg.deleteLater()
    app.processEvents()


def test_match_painted_during_round_pulses_when_round_closes() -> None:
    """Local Auto fetch paints MATCH while the round is open; pulse starts on close.

    That is the "orange animation bar is gone" regression: promotion refused
    SYNCING-window MATCH rows, then ``repaint=False`` left them stranded.
    """
    app, dlg = _dialog()
    dlg._auto_fetch_round_active = True
    dlg._mark_meters_fetched()
    dlg._machine_state = {"coinin": "1100", "coinout": "50"}
    dlg._local_sas_state = {"coinin": "1100", "coinout": "50"}
    dlg._render(parsed_rows=dlg._last_parsed_rows, allow_machine_lookup=True)

    assert dlg._table.item(_row_for_code(dlg, "0000"), COL_STATUS).text() == "MATCH"
    assert "0000" in dlg._meter_flash_pending
    assert "0000" not in dlg._meter_flash_keys
    assert not _row_has_flash_fill(dlg, _row_for_code(dlg, "0000"))

    dlg._end_auto_fetch_round(repaint=False)

    assert dlg._compare_sources_settled() is True
    assert "0000" in dlg._meter_flash_keys
    assert _row_has_flash_fill(dlg, _row_for_code(dlg, "0000"))

    dlg.deleteLater()
    app.processEvents()