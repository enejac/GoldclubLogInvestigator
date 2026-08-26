"""Meter tabs are built eagerly so Accounting keeps the verify table."""

from __future__ import annotations

from pathlib import Path

from gui.sas_verify_dialog import TAB_ACCOUNTING, _METER_TAB_NAMES


ROOT = Path(__file__).resolve().parents[1]
SAS = ROOT / "gui" / "sas_verify_dialog.py"


def test_accounting_tab_is_meters_table_not_game_panel() -> None:
    text = SAS.read_text(encoding="utf-8")
    assert "_ensure_meter_tab" not in text
    assert "self._meter_tabs.addTab(self._accounting_tab" in text
    assert "wrap_expand_verify_table_in_group_box(box, self._table)" in text
    assert _METER_TAB_NAMES[TAB_ACCOUNTING] == "Accounting"
