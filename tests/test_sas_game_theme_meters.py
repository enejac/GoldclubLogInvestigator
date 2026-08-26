"""Per-game Game-tab meters must resolve catalog Id vs DeviceManagerData themeId."""

from __future__ import annotations

from network.accounting_state_loader import (
    aggregate_theme_paytable_meters,
    load_theme_paytable_ids,
    resolve_theme_perf_meters,
    theme_id_match_key,
)


def test_theme_id_match_key_collapses_spaces_and_underscores() -> None:
    assert theme_id_match_key("Roulette Game") == theme_id_match_key("RouletteGame")
    assert theme_id_match_key("Sizzling Sevens HD HnW") == theme_id_match_key(
        "SizzlingSevensHD_HnW"
    )


def test_resolve_theme_perf_meters_matches_catalog_id_to_theme_path() -> None:
    perf = {
        "RouletteGame": {
            "return_97_0": {"coinin": "170", "gamesplayed": "3"},
        },
        "SizzlingSevensHD_HnW": {
            "return_94_0": {"coinin": "50", "gamesplayed": "1"},
        },
        "PR3_RedZone": {
            "return_94_0": {"coinin": "10", "gamesplayed": "2"},
        },
    }
    # Catalog shows "Roulette Game" while XML themeId / ThemePath is RouletteGame.
    roulette = resolve_theme_perf_meters(
        perf, "Roulette Game", folder="RouletteGame"
    )
    assert aggregate_theme_paytable_meters(roulette)["coinin"] == "170"
    assert roulette["return_97_0"]["gamesplayed"] == "3"

    sizzling = resolve_theme_perf_meters(
        perf, "Sizzling Sevens HD HnW", folder="SizzlingSevensHD_HnW"
    )
    assert sizzling["return_94_0"]["coinin"] == "50"

    # Exact catalog Id still works.
    assert resolve_theme_perf_meters(perf, "PR3_RedZone")["return_94_0"]["coinin"] == "10"
    assert resolve_theme_perf_meters(perf, "Missing Game", folder="NoFolder") == {}


def test_load_theme_paytable_ids_uses_resolved_perf_keys(tmp_path) -> None:
    perf = {
        "RouletteGame": {"return_97_0": {"coinin": "1"}, "return_96_0": {"coinin": "2"}},
    }
    ids = load_theme_paytable_ids(
        str(tmp_path),
        "Roulette Game",
        perf_by_paytable=perf,
        catalog_folders={"Roulette Game": "RouletteGame"},
    )
    assert ids == ["return_96_0", "return_97_0"]


def test_game_meter_state_uses_resolved_theme_for_roulette() -> None:
    from types import SimpleNamespace

    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import GAME_THEME_TOTAL, SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(
            current_product_name="GUI-Test",
            get_gm2u_value_for_sas_code=lambda c, s: "",
        ),
        QThreadPool.globalInstance(),
        scan_root="",
    )
    dlg._prefetch_started = True
    dlg._theme_perf_by_paytable = {
        "RouletteGame": {
            "return_97_0": {"coinin": "170", "gamesplayed": "3", "coinout": "40"},
        }
    }
    dlg._game_catalog_folders = {"Roulette Game": "RouletteGame"}
    blocked = dlg._game_theme_combo.blockSignals(True)
    dlg._game_theme_combo.clear()
    dlg._game_theme_combo.addItem(GAME_THEME_TOTAL)
    dlg._game_theme_combo.addItem("Roulette Game")
    dlg._game_theme_combo.setCurrentIndex(1)
    dlg._game_theme_combo.blockSignals(blocked)
    blocked = dlg._game_paytable_combo.blockSignals(True)
    dlg._game_paytable_combo.clear()
    dlg._game_paytable_combo.addItem(GAME_THEME_TOTAL)
    dlg._game_paytable_combo.setCurrentIndex(0)
    dlg._game_paytable_combo.blockSignals(blocked)

    state, filtered = dlg._game_meter_state(allow_machine_lookup=False)
    assert filtered is True
    assert state["coinin"] == "170"
    assert state["gamesplayed"] == "3"

    dlg._update_game_summary(allow_machine_lookup=False)
    assert dlg._game_perf_labels["played"].text() == "3"
    # Bet is shown as money when Show $ is on; accept credits or dollars.
    bet = dlg._game_perf_labels["bet"].text()
    assert "170" in bet or "1.70" in bet

    dlg.deleteLater()
    app.processEvents()