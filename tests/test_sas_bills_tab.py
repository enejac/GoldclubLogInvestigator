"""Bills tab must always show the denomination catalog (never a blank panel)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from gui.sas_verify_dialog import (
    _BILLS_CATALOG_ROW_COUNT,
    SasVerifyDialog,
    prepare_bill_table_body_rows,
    verify_bills_table_spec,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _dialog(app, scan_root: str = "") -> SasVerifyDialog:
    vm = SimpleNamespace(
        current_product_name="test",
        get_gm2u_value_for_sas_code=lambda c, s: "",
    )
    dlg = SasVerifyDialog(vm, QThreadPool.globalInstance(), scan_root=scan_root)
    dlg._prefetch_started = True
    dlg._auto_fetch_toggle.setChecked(False)
    return dlg


def test_bills_tab_seeds_full_catalog_on_open(app) -> None:
    dlg = _dialog(app)
    assert dlg._bills_table.rowCount() == _BILLS_CATALOG_ROW_COUNT + 1
    assert dlg._bills_out_table.rowCount() == _BILLS_CATALOG_ROW_COUNT + 1
    assert dlg._bills_table.item(0, 0).text()
    assert dlg._bills_table.item(_BILLS_CATALOG_ROW_COUNT, 0).text() == "TOTAL"
    assert dlg._bills_out_table.item(_BILLS_CATALOG_ROW_COUNT, 0).text() == "TOTAL"
    assert not verify_bills_table_spec(
        column_count=dlg._bills_table.columnCount(),
        headers=tuple(
            dlg._bills_table.horizontalHeaderItem(i).text()
            for i in range(dlg._bills_table.columnCount())
        ),
        vertical_header_hidden=dlg._bills_table.verticalHeader().isHidden(),
        row_count=dlg._bills_table.rowCount(),
    )


def test_empty_bill_render_does_not_leave_a_blank_panel(app) -> None:
    dlg = _dialog(app)
    # Simulate the old damage path: a real capture, then an empty re-render.
    dlg._render_bills()
    tall = dlg._bills_table.height()
    dlg._last_bill_in_rows = []
    dlg._last_bill_out_rows = []
    dlg._render_bill_table(dlg._bills_table, [], direction="in")
    dlg._render_bill_table(dlg._bills_out_table, [], direction="out")
    assert dlg._bills_table.rowCount() == _BILLS_CATALOG_ROW_COUNT + 1
    assert dlg._bills_out_table.rowCount() == _BILLS_CATALOG_ROW_COUNT + 1
    # Height must shrink to content, not keep a previous tall empty frame.
    assert dlg._bills_table.height() <= tall + 2
    assert dlg._bills_table.height() > dlg._bills_table.horizontalHeader().height()


def test_clear_all_reseeds_bill_catalog(app) -> None:
    dlg = _dialog(app)
    dlg._clear_all()
    assert dlg._bills_table.rowCount() == _BILLS_CATALOG_ROW_COUNT + 1
    assert dlg._bills_out_table.rowCount() == _BILLS_CATALOG_ROW_COUNT + 1
    assert dlg._bills_table.item(_BILLS_CATALOG_ROW_COUNT, 0).text() == "TOTAL"


def test_prepare_empty_rows_expands_to_catalog() -> None:
    body_in, totals_in = prepare_bill_table_body_rows([], direction="in")
    body_out, totals_out = prepare_bill_table_body_rows([], direction="out")
    assert totals_in is None and totals_out is None
    assert len(body_in) == _BILLS_CATALOG_ROW_COUNT
    assert len(body_out) == _BILLS_CATALOG_ROW_COUNT


def test_render_paints_bills_from_machine_note_meters(app) -> None:
    """Local Auto fetch only calls ``_render`` — Bills must still fill from notes."""
    from gui.sas_verify_dialog import Sas6FRow

    dlg = _dialog(app)
    dlg._machine_state = {
        "notecurincnt500": "3",
        "notecurinamt500": "1500",
        "coinin": "1000",
    }
    dlg._machine_state_loaded = True
    dlg._last_bill_in_rows = []  # as after a local round that never COM-captured
    dlg._render(
        parsed_rows=[Sas6FRow(meter_id="0000", sas_value_text="1000")],
        allow_machine_lookup=True,
    )
    # Find the $5.00 body row (not TOTAL).
    five_count = None
    for row in range(dlg._bills_table.rowCount()):
        label = dlg._bills_table.item(row, 0)
        if label and label.text() == "$5.00":
            five_count = dlg._bills_table.item(row, 2).text()
            break
    assert five_count == "3"
    dlg.deleteLater()
    app.processEvents()
def test_bills_and_coins_count_column_matches_meter_panel_width(app) -> None:
    """COUNT is compact (Master/Game-sized), not stretched across the tab."""
    from PySide6.QtWidgets import QHeaderView

    from gui.sas_verify_dialog import (
        _BILLS_COL_COUNT_IDX,
        _COINS_COL_COUNT_IDX,
        _METER_COUNT_MIN_WIDTH,
        bills_coins_column_widths,
        fit_bills_coins_table_columns,
    )

    dlg = _dialog(app)
    label_w, amount_w, count_w = bills_coins_column_widths()
    assert count_w == _METER_COUNT_MIN_WIDTH

    for table in (dlg._bills_table, dlg._bills_out_table, *dlg._coin_tables.values()):
        assert table.horizontalHeader().stretchLastSection() is False
        assert table.columnWidth(_BILLS_COL_COUNT_IDX) == count_w
        assert table.columnWidth(0) == label_w
        assert table.columnWidth(1) == amount_w
        mode = table.horizontalHeader().sectionResizeMode(_BILLS_COL_COUNT_IDX)
        assert mode == QHeaderView.ResizeMode.Fixed
        # Table width is locked to the three columns (no empty COUNT stretch).
        assert table.maximumWidth() == table.minimumWidth()
        assert table.maximumWidth() <= label_w + amount_w + count_w + 8

    # Re-fit after a refresh must not change geometry (no jump).
    before = (
        dlg._bills_table.columnWidth(_BILLS_COL_COUNT_IDX),
        dlg._bills_table.width(),
        dlg._bills_table.minimumWidth(),
    )
    dlg._render_bills()
    dlg._render_coins()
    fit_bills_coins_table_columns(dlg._bills_table)
    after = (
        dlg._bills_table.columnWidth(_BILLS_COL_COUNT_IDX),
        dlg._bills_table.width(),
        dlg._bills_table.minimumWidth(),
    )
    assert after == before
    # Coins COUNT uses the same index/width contract.
    assert dlg._coin_tables["in"].columnWidth(_COINS_COL_COUNT_IDX) == count_w
    dlg.deleteLater()
    app.processEvents()
