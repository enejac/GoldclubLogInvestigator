"""Burst-mode visuals for rapid play (option Q / ~3 games/s)."""

from __future__ import annotations

import time
from types import SimpleNamespace

from PySide6.QtCore import Qt

from gui.sas_verify_dialog import (
    BURST_ENTER_CLUSTER_COUNT,
    BURST_ENTER_CLUSTER_WINDOW_S,
    BURST_EXIT_IDLE_S,
    COL_6F_CODE,
    COL_COUNT,
    COL_MACHINE_VALUE,
    COL_STATUS,
    METER_FLASH_BURST_DURATION_S,
    METER_FLASH_DURATION_S,
    METER_FLASH_HOLD_S,
    SAS_VERIFY_HELP_HTML,
    burst_mode_should_be_on,
    estimate_landing_rate,
    format_burst_activity_strip,
    meter_flash_strength,
    meter_value_delta,
)


_ALIASES = {"0000": "coinin", "0001": "coinout", "0005": "gamesplayed"}


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
    dlg._machine_state = {
        "coinin": "1000",
        "coinout": "50",
        "gamesplayed": "100",
    }
    dlg._machine_state_loaded = True
    dlg._loaded_cabinet_scan_root = root
    dlg._machine_state_loaded_at = time.monotonic()
    dlg._on_local_diff_done(
        "",
        {"coinin": "1000", "coinout": "50", "gamesplayed": "100"},
    )
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


def test_estimate_landing_rate_from_timestamps() -> None:
    assert estimate_landing_rate([]) == 0.0
    assert estimate_landing_rate([1.0]) == 0.0
    # Three landings over 1.0 s → 2 games/s.
    assert abs(estimate_landing_rate([10.0, 10.5, 11.0]) - 2.0) < 1e-9


def test_burst_mode_enters_on_rate_and_exits_after_idle() -> None:
    now = 100.0
    # 3 landings in 1 s → 2 games/s ≥ 1.0
    stamps = [99.0, 99.5, 100.0]
    assert burst_mode_should_be_on(stamps, now=now, currently_on=False) is True
    assert burst_mode_should_be_on(stamps, now=now, currently_on=True) is True
    # Still on while idle < exit window.
    assert (
        burst_mode_should_be_on(
            stamps, now=now + BURST_EXIT_IDLE_S - 0.1, currently_on=True
        )
        is True
    )
    assert (
        burst_mode_should_be_on(
            stamps, now=now + BURST_EXIT_IDLE_S + 0.05, currently_on=True
        )
        is False
    )


def test_burst_mode_enters_on_tight_cluster() -> None:
    now = 50.0
    # Rate alone is low (span large), but 3 hits inside the cluster window.
    stamps = [now - 0.4, now - 0.2, now]
    assert len(stamps) >= BURST_ENTER_CLUSTER_COUNT
    assert (stamps[-1] - stamps[0]) <= BURST_ENTER_CLUSTER_WINDOW_S
    assert burst_mode_should_be_on(stamps, now=now, currently_on=False) is True


def test_burst_flash_envelope_is_short() -> None:
    assert METER_FLASH_BURST_DURATION_S < 1.0
    assert METER_FLASH_BURST_DURATION_S < METER_FLASH_DURATION_S
    assert meter_flash_strength(0.0) == 1.0
    assert (
        meter_flash_strength(
            0.0,
            duration_s=METER_FLASH_BURST_DURATION_S,
            hold_s=0.12,
        )
        == 1.0
    )
    assert (
        meter_flash_strength(
            METER_FLASH_BURST_DURATION_S,
            duration_s=METER_FLASH_BURST_DURATION_S,
            hold_s=0.12,
        )
        == 0.0
    )
    # Normal envelope still holds the long standing-at-cabinet pulse.
    assert meter_flash_strength(METER_FLASH_HOLD_S) == 1.0
    assert meter_flash_strength(2.0) > 0.25


def test_format_burst_activity_strip() -> None:
    text = format_burst_activity_strip(
        games_per_s=2.8,
        delta_games=12,
        delta_coinin=480,
        last_ago_s=0.18,
    )
    assert "Burst ~2.8 games/s" in text
    assert "Δgames +12" in text
    assert "Δcoin-in +480" in text
    assert "last 180 ms ago" in text


def test_meter_value_delta() -> None:
    assert meter_value_delta("100", "112") == 12
    assert meter_value_delta("", "5") == 0
    assert meter_value_delta("10", "9") == 0


def test_dialog_enters_burst_and_shows_strip() -> None:
    app, dlg = _dialog()
    # Simulate three rapid Machine-side increases (option Q pace).
    for games, coinin in ((101, 1040), (102, 1080), (103, 1120)):
        dlg._mark_meters_fetched()
        dlg._machine_state = {
            "coinin": str(coinin),
            "coinout": "50",
            "gamesplayed": str(games),
        }
        dlg._local_sas_state = dict(dlg._machine_state)
        dlg._render(parsed_rows=dlg._last_parsed_rows, allow_machine_lookup=True)

    assert dlg._burst_mode is True
    assert not dlg._burst_activity_label.isHidden()
    assert "Burst" in dlg._burst_activity_label.text()
    assert "Δgames" in dlg._burst_activity_label.text()
    # Short envelope while bursting.
    duration, hold = dlg._flash_envelope()
    assert duration == METER_FLASH_BURST_DURATION_S
    assert hold < METER_FLASH_HOLD_S

    dlg.deleteLater()
    app.processEvents()


def test_burst_match_pulse_uses_short_envelope() -> None:
    app, dlg = _dialog()
    dlg._burst_mode = True
    dlg._sync_flash_timer_interval()
    dlg._mark_meters_fetched()
    dlg._machine_state = {
        "coinin": "1100",
        "coinout": "50",
        "gamesplayed": "101",
    }
    dlg._local_sas_state = dict(dlg._machine_state)
    dlg._render(parsed_rows=dlg._last_parsed_rows, allow_machine_lookup=True)

    row = _row_for_code(dlg, "0000")
    assert dlg._table.item(row, COL_STATUS).text() == "MATCH"
    assert "0000" in dlg._meter_flash_keys
    assert _row_has_flash_fill(dlg, row)
    # After the short burst duration the fill must be gone.
    dlg._meter_flash_started_mono = time.monotonic() - (
        METER_FLASH_BURST_DURATION_S + 0.05
    )
    dlg._on_meter_flash_tick()
    assert "0000" not in dlg._meter_flash_keys
    assert not _row_has_flash_fill(dlg, row)

    dlg.deleteLater()
    app.processEvents()


def test_burst_mode_off_restores_normal_min_refresh() -> None:
    app, dlg = _dialog()
    dlg._burst_mode = False
    assert dlg._auto_fetch_min_refresh_s() == 0.35
    dlg._burst_mode = True
    assert dlg._auto_fetch_min_refresh_s() == 0.20
    dlg._burst_mode = False
    assert dlg._auto_fetch_min_refresh_s() == 0.35
    dlg.deleteLater()
    app.processEvents()


def test_help_mentions_burst_strip() -> None:
    assert "Burst" in SAS_VERIFY_HELP_HTML
    assert "option Q" in SAS_VERIFY_HELP_HTML
    assert "games/s" in SAS_VERIFY_HELP_HTML
