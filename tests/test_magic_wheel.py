"""Magic Wheel meter submenu: config + DeviceManager values match EGM UI."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

FIXTURE_SCAN_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "magicwheel_cabinet"
    / "Goldclub"
    / "var"
    / "log"
)

# Values captured from GST20664 MagicWheel meter tab (2026-07-31).
EXPECTED = {
    "money_limit": 500,
    "money_wheel_average": 5,
    "game_theme_id": "Roulette Game",
    "times_triggered": 1,
    "total_num_spins": 8,
    "total_win_amount": 3960,
    "avg_win_amount": 495,
    "credit_list": (0, 10, 50, 100, 250, 500, 1000, 2000),
    "wheel_segments": (1000, 250, 100, 500, 50, 1000, 100, 0, 2000, 10),
}


def test_parse_helpers_match_cabinet_xml() -> None:
    from network.magic_wheel_loader import (
        avg_win_amount,
        credit_list_from_segments,
        parse_magic_wheel_config,
        parse_magic_wheel_pack_settings,
        parse_magic_wheel_segments,
    )

    themes = FIXTURE_SCAN_ROOT.parents[1] / "slot" / "themes"
    money_limit, enabled = parse_magic_wheel_pack_settings(
        (themes / "jurisdiction_config.xml").read_text(encoding="utf-8-sig")
    )
    assert money_limit == 500
    assert enabled is True
    assert parse_magic_wheel_config(
        (themes / "magicwheel_Config.xml").read_text(encoding="utf-8-sig")
    ) == 5
    segs = parse_magic_wheel_segments(
        (themes / "MagicWheel.xml").read_text(encoding="utf-8-sig")
    )
    assert segs == EXPECTED["wheel_segments"]
    assert credit_list_from_segments(segs) == EXPECTED["credit_list"]
    assert avg_win_amount(total_won=3960, num_spins=8) == 495


def test_fixture_load_matches_egm_screenshot() -> None:
    from network.magic_wheel_loader import load_magic_wheel_data

    data = load_magic_wheel_data(str(FIXTURE_SCAN_ROOT))
    assert data.money_limit == EXPECTED["money_limit"]
    assert data.money_wheel_average == EXPECTED["money_wheel_average"]
    assert data.game_theme_id == EXPECTED["game_theme_id"]
    assert data.times_triggered == EXPECTED["times_triggered"]
    assert data.total_num_spins == EXPECTED["total_num_spins"]
    assert data.total_win_amount == EXPECTED["total_win_amount"]
    assert data.avg_win_amount == EXPECTED["avg_win_amount"]
    assert data.credit_list == EXPECTED["credit_list"]
    assert data.wheel_segments == EXPECTED["wheel_segments"]
    assert data.money_limit_display == "$500"
    assert data.money_wheel_avg_display == "$5"


def test_live_cabinet_90_matches_screenshot_when_reachable() -> None:
    """Non-exe live check against .90 — skip if share unreachable."""
    from network.magic_wheel_loader import load_magic_wheel_data

    scan = r"\\10.0.0.90\c$\Goldclub\var\log"
    jur = Path(r"\\10.0.0.90\c$\Goldclub\slot\themes\jurisdiction_config.xml")
    if not jur.is_file():
        import pytest

        pytest.skip("cabinet share unreachable")
    data = load_magic_wheel_data(scan)
    # Config + wheel geometry must match the screenshot/config on .90.
    assert data.money_limit == 500
    assert data.money_wheel_average == 5
    assert data.wheel_segments == EXPECTED["wheel_segments"]
    assert data.credit_list == EXPECTED["credit_list"]
    assert data.game_theme_id == "Roulette Game"
    assert data.config_setup is True
    # Live meters are optional after RAM clear / unused wheel — assert shape when present.
    if data.times_triggered is None:
        import pytest

        pytest.skip("MagicWheel meters not populated on .90 yet (config OK)")
    assert data.times_triggered >= 1
    assert data.total_num_spins is not None and data.total_num_spins >= 1
    assert data.total_win_amount is not None and data.total_win_amount >= 0
    assert data.avg_win_amount == data.total_win_amount // data.total_num_spins


def test_magicwheel_tab_renders_fixture_values() -> None:
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import TAB_MAGICWHEEL, SasVerifyDialog, _METER_TAB_NAMES
    from network.magic_wheel_loader import load_magic_wheel_data

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=str(FIXTURE_SCAN_ROOT),
    )
    dlg._prefetch_started = True
    assert _METER_TAB_NAMES[TAB_MAGICWHEEL] == "MagicWheel"
    assert dlg._meter_tabs.tabText(TAB_MAGICWHEEL) == "MagicWheel"

    dlg._scan_root = str(FIXTURE_SCAN_ROOT)
    blocked = dlg._scan_root_edit.blockSignals(True)
    dlg._scan_root_edit.setText(str(FIXTURE_SCAN_ROOT))
    dlg._scan_root_edit.blockSignals(blocked)
    dlg._machine_state_loaded = True
    dlg._update_magicwheel_summary(allow_machine_lookup=True)
    app.processEvents()

    h = dlg._magicwheel_handles
    data = load_magic_wheel_data(str(FIXTURE_SCAN_ROOT))
    assert h["money_limit"].text() == "$500"
    assert h["money_avg"].text() == "$5"
    assert h["game"].currentText() == "Roulette Game"
    assert h["times"].text() == "1"
    assert h["spins"].text() == "8"
    assert h["total_win"].text() == "3960"
    assert h["avg_win"].text() == "495"
    credits = [h["credits_list"].item(i).text() for i in range(h["credits_list"].count())]
    assert credits == [f"Credits: {c}" for c in data.credit_list]
    assert h["wheel"]._segments == data.wheel_segments

    dlg.deleteLater()
    app.processEvents()


def test_config_setup_gate_requires_money_wheel_average() -> None:
    from network.magic_wheel_loader import (
        is_magic_wheel_config_setup,
        magic_wheel_config_xml_is_setup,
    )

    assert magic_wheel_config_xml_is_setup(
        "<MagicWheelSettingsConfig><MoneyWheelAverage>5</MoneyWheelAverage>"
        "</MagicWheelSettingsConfig>"
    )
    assert not magic_wheel_config_xml_is_setup("<MagicWheelSettingsConfig></MagicWheelSettingsConfig>")
    assert not magic_wheel_config_xml_is_setup("<Other><MoneyWheelAverage>5</MoneyWheelAverage></Other>")
    assert is_magic_wheel_config_setup(str(FIXTURE_SCAN_ROOT))
    assert not is_magic_wheel_config_setup("")
    assert not is_magic_wheel_config_setup(str(Path(__file__).resolve().parent / "fixtures" / "missing"))


def test_magicwheel_tab_hidden_without_config(tmp_path) -> None:
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import TAB_MAGICWHEEL, SasVerifyDialog

    # Minimal Goldclub tree without magicwheel_Config.xml
    log = tmp_path / "Goldclub" / "var" / "log"
    log.mkdir(parents=True)
    (tmp_path / "Goldclub" / "slot" / "themes").mkdir(parents=True)

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=str(log),
    )
    dlg._prefetch_started = True
    assert not dlg._meter_tab_is_visible(TAB_MAGICWHEEL)
    dlg._goto_meter_tab(TAB_MAGICWHEEL)
    assert dlg._meter_tabs.currentIndex() != TAB_MAGICWHEEL

    # Point at fixture with config -> tab appears
    blocked = dlg._scan_root_edit.blockSignals(True)
    dlg._scan_root_edit.setText(str(FIXTURE_SCAN_ROOT))
    dlg._scan_root_edit.blockSignals(blocked)
    dlg._scan_root = str(FIXTURE_SCAN_ROOT)
    dlg._sync_magicwheel_tab_visibility()
    assert dlg._meter_tab_is_visible(TAB_MAGICWHEEL)
    dlg.deleteLater()
    app.processEvents()


def test_magicwheel_tab_visible_with_fixture_config() -> None:
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import TAB_MAGICWHEEL, SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=str(FIXTURE_SCAN_ROOT),
    )
    dlg._prefetch_started = True
    assert dlg._meter_tab_is_visible(TAB_MAGICWHEEL)
    dlg.deleteLater()
    app.processEvents()
