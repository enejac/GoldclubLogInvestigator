"""Guards: Config Scanner can restore a selected snapshot without a compare."""

from __future__ import annotations

from pathlib import Path

from config_scanner.write_scope import WriteScope
from gui.config_scanner_tab import (
    SNAPSHOT_DRAWER_MIN_OPEN_PX,
    SNAPSHOT_TABLE_ROW_PX,
    SnapshotTableModel,
    SnapshotVersionDelegate,
    configure_snapshot_table_view,
    full_snapshot_saved_message,
    resize_snapshot_table_columns_to_contents,
    snapshot_context_advanced_restore_actions,
    snapshot_context_primary_restore_label,
    snapshot_context_restore_actions,
    snapshot_table_row_from_pos,
    snapshot_version_build_parts,
    snapshots_drawer_open_sizes,
)

SRC = (
    Path(__file__).resolve().parents[1] / "gui" / "config_scanner_tab.py"
).read_text(encoding="utf-8")


def test_create_full_snapshot_is_a_toolbar_action() -> None:
    assert 'QPushButton("Create full snapshot")' in SRC
    assert 'QAction("Create full snapshot", self)' in SRC
    assert "_on_create_snapshot_clicked" in SRC
    assert "_start_live_scan(compare_after=False, include_software=True)" in SRC
    assert "_start_live_scan(compare_after=True, include_software=False)" in SRC
    assert "_pending_create_snapshot" in SRC
    assert "_finish_create_full_snapshot" in SRC
    assert 'QPushButton("Restore snapshot")' in SRC
    create_at = SRC.find('QPushButton("Create full snapshot")')
    restore_at = SRC.find('QPushButton("Restore snapshot")')
    scan_at = SRC.find('QPushButton("Scan && compare")')
    assert create_at != -1 and restore_at != -1 and create_at < restore_at
    assert scan_at != -1 and restore_at < scan_at
    assert "self._scan_btn.hide()" in SRC
    assert "not self._pending_create_snapshot" in SRC
    assert "order_snapshots_newest_first" in SRC
    assert "pin_name=self._last_scan_snapshot_name" in SRC
    assert "EnsureVisible" in SRC
    assert "configScannerSnapshotTable" in SRC
    assert "_snapshot_sel_guard" in SRC
    assert "pressed.connect" not in SRC
    assert ".setUniformRowHeights" not in SRC
    assert "configure_snapshot_table_view" in SRC
    assert "NoEditTriggers" in SRC
    assert "snapshot_table_row_from_pos" in SRC
    assert "self._snapshot_table.setEnabled(True)" in SRC
    assert "self._snapshot_table.setEnabled(not busy)" not in SRC
    assert "viewport().installEventFilter" in SRC
    assert 'QMessageBox.information(\n            self,\n            "Snapshot saved"' in SRC or (
        '"Snapshot saved"' in SRC and "_finish_create_full_snapshot" in SRC
    )


def test_full_snapshot_saved_message_includes_software() -> None:
    text = full_snapshot_saved_message(
        "2026-08-21_GRT330106_v10.2_b40119",
        457,
        software_file_count=7,
        software_captured=True,
    )
    assert "This machine is saved." in text
    assert "2026-08-21_GRT330106_v10.2_b40119" in text
    assert "457 config files" in text
    assert "7 Ruleta software files (exe + Godot)" in text
    assert "The machine was not changed." in text
    assert "Restore snapshot" in text
    assert "Scan completed with warnings" not in text
    missing = full_snapshot_saved_message("x", 1, software_captured=False)
    assert "software was not included" in missing
    with_note = full_snapshot_saved_message(
        "x",
        2,
        software_captured=True,
        software_file_count=7,
        extra_notes=("Captured 7 Ruleta software files into the snapshot.", "real issue"),
    )
    assert "Captured 7" not in with_note
    assert "real issue" in with_note


def test_restore_selected_is_on_snapshots_drawer() -> None:
    assert "Restore to machine" in SRC
    assert "_on_restore_selected_clicked" in SRC
    assert "_on_restore_selected_software_clicked" in SRC
    assert "_on_restore_selected_config_clicked" in SRC
    assert "_snapshot_name_for_restore" in SRC
    assert "Restore config only…" in SRC
    assert 'QPushButton("Restore config only")' in SRC
    assert "Restore software only (keep profile)…" in SRC
    assert "_on_restore_selected_binaries_clicked" in SRC
    assert "self._write_scope_combo.hide()" in SRC
    assert "restore_sel_row.addWidget(self._restore_selected_btn" in SRC
    restore_at = SRC.find("restore_sel_row.addWidget(self._restore_selected_btn")
    table_at = SRC.find("drawer_layout.addWidget(self._snapshot_table")
    assert restore_at != -1 and table_at != -1 and restore_at < table_at
    assert "write_scope=WriteScope.FULL_SOFTWARE" in SRC


def test_snapshot_context_menu_offers_full_and_ruleta_software() -> None:
    labels = [label for label, _scope in snapshot_context_restore_actions()]
    assert labels == [
        "Restore to machine (config + software)…",
        "Restore config only…",
        "Restore software only (keep profile)…",
    ]
    scopes = [scope for _label, scope in snapshot_context_restore_actions()]
    assert scopes == [
        WriteScope.FULL_SOFTWARE,
        WriteScope.FULL,
        WriteScope.BINARIES_ONLY,
    ]


def test_snapshot_context_primary_restore_is_full_software() -> None:
    assert snapshot_context_primary_restore_label() == (
        "Restore to machine (config + software)…"
    )
    advanced = [scope for _label, scope in snapshot_context_advanced_restore_actions()]
    assert WriteScope.FULL_SOFTWARE not in advanced


def test_snapshot_context_menu_right_click_offers_config_only() -> None:
    """Right-click: config+software first, then config-only. Software-only stays in More."""
    handler = SRC.split("def _on_snapshot_context_menu")[1].split("\n    def ", 1)[0]
    assert "snapshot_context_primary_restore_label()" in handler
    assert "write_scope=WriteScope.FULL_SOFTWARE" in handler
    assert "write_scope=WriteScope.FULL" in handler
    assert "write_scope=WriteScope.BINARIES_ONLY" not in handler
    assert "snapshot_context_restore_actions(" not in handler


def test_snapshot_context_menu_appends_other_combo_scope() -> None:
    labels = [
        label
        for label, _scope in snapshot_context_restore_actions(WriteScope.HARDWARE)
    ]
    assert "Restore hardware to machine…" in labels
    assert labels[0] == "Restore to machine (config + software)…"
    assert labels[1] == "Restore config only…"


def test_snapshots_drawer_opens_wide_enough_for_full_names() -> None:
    left, right = snapshots_drawer_open_sizes(1600)
    assert left >= SNAPSHOT_DRAWER_MIN_OPEN_PX
    assert left + right == 1600
    wide_left, wide_right = snapshots_drawer_open_sizes(1920)
    assert wide_left >= int(1920 * 0.45)
    assert wide_left + wide_right == 1920
    assert "_expand_snapshots_drawer" in SRC
    assert "resize_snapshot_table_columns_to_contents" in SRC


def test_revert_uses_recorded_write_scope() -> None:
    assert "revert_scope = info.write_scope" in SRC
    assert "write_scope=revert_scope" in SRC
    assert "Restore that undo snapshot" in SRC


def test_swap_confirm_asks_backup_in_the_same_dialog() -> None:
    assert "Swap this machine to:" in SRC
    assert "Save a backup of what is running now" in SRC
    assert "How it works:" not in SRC
    assert "Config Scanner — confirm restore" in SRC


def test_live_ruleta_version_banner_is_bold() -> None:
    assert 'setObjectName("liveRuletaVersion")' in SRC
    assert "format_live_ruleta_sw_banner" in SRC
    assert "_refresh_live_ruleta_version" in SRC
    assert "font-weight: 700" in SRC
    assert "live_ruleta_exe_version_for_target" in SRC


def test_pre_restore_undo_point_captures_ruleta_binaries() -> None:
    """The snapshot taken before a restore must be able to put the exe back."""
    marker = "self._pending_include_software = True"
    assert marker in SRC
    at = SRC.find(marker)
    write = SRC.find("self._pending_write_snapshot = snapshot_name", at)
    assert write != -1 and write - at < 200
    assert "_pending_include_software = False\n        self._pending_write_snapshot" not in SRC


def test_restore_stops_when_undo_point_has_no_binaries() -> None:
    assert "_confirm_incomplete_undo_point" in SRC
    assert "_software_swapping_scope" in SRC
    assert "WriteScope.FULL_SOFTWARE, WriteScope.BINARIES_ONLY" in SRC
    assert "result.software_captured" in SRC
    assert "undo point is incomplete" in SRC
    assert "Restore cancelled — undo point has no Ruleta binaries." in SRC


def test_revert_ui_says_what_it_puts_back() -> None:
    assert "get_rollback_details" in SRC
    assert "puts back " in SRC
    assert "details.summary()" in SRC
    assert "is_self_contained" in SRC


def test_auto_start_stack_is_optional_kill_is_not() -> None:
    assert "Auto-start stack" in SRC
    assert "_auto_start_stack_cb" in SRC
    assert "should_autostart_after_write" in SRC
    assert "Stopping GoldClub stack before restore" in SRC
    assert "Start GoldClub stack…" in SRC
    assert "_on_start_stack_clicked" in SRC


def test_restore_does_not_auto_prompt_llave_code() -> None:
    """Bound 10.1↔10.2 restore must not pop the LLAVE CODE box after Run-FullStack."""
    apply_fn = SRC.split("def _on_apply_finished", 1)[1].split("\n    def ", 1)[0]
    assert "_offer_llave_after_restart = True" not in apply_fn
    assert "More → Enter trial password (LLAVE)" in SRC


def test_restore_defers_dll_lock_when_kill_all_will_run() -> None:
    assert "defer_lock_check=plan is not None" in SRC
    assert "check_dll_lock=not defer_lock_check" in (
        Path(__file__).resolve().parents[1]
        / "config_scanner"
        / "service.py"
    ).read_text(encoding="utf-8")


def test_start_llave_kwargs_computed_before_pending_snapshot_clear() -> None:
    """Bound snapshot restores must not auto-type LLAVE after apply clears pending."""
    apply_fn = SRC.split("def _on_apply_finished", 1)[1].split("\n    def ", 1)[0]
    kwargs_idx = apply_fn.find('_stack_trial_kwargs(phase="start")')
    clear_idx = apply_fn.find("self._pending_write_snapshot = None")
    assert kwargs_idx != -1 and clear_idx != -1
    assert kwargs_idx < clear_idx
    assert "snapshot_should_auto_enter_llave" in SRC
    assert "ensure_llave" in SRC


def test_more_menu_offers_clear_error30() -> None:
    assert "Clear ERROR 30 / 99 trial leftovers…" in SRC
    assert "_on_clear_error30_clicked" in SRC
    assert "never overwrite" in SRC.lower() or "never overwritten" in SRC.lower()


def test_context_menu_passes_write_scope_override() -> None:
    handler = SRC.split("def _on_snapshot_context_menu")[1]
    assert "snapshot_context_primary_restore_label()" in SRC
    assert "write_scope=WriteScope.FULL_SOFTWARE" in handler
    assert "write_scope=WriteScope.FULL" in handler
    assert "write_scope: WriteScope | str | None = None" in SRC


def test_snapshot_version_build_parts_bolds_only_fourth() -> None:
    assert snapshot_version_build_parts("10.2.0.876") == ("10.2.0", "876")
    assert snapshot_version_build_parts("10.2.0.827") == ("10.2.0", "827")
    assert snapshot_version_build_parts("10.2.0") == ("10.2.0", None)
    assert snapshot_version_build_parts("10.2") == ("10.2", None)
    assert snapshot_version_build_parts("—") == ("—", None)
    assert snapshot_version_build_parts("") == ("—", None)


def test_snapshot_version_delegate_is_wired_to_version_column() -> None:
    assert "setItemDelegateForColumn" in SRC
    assert "SnapshotVersionDelegate" in SRC
    assert SnapshotTableModel.VERSION_COL == 2


def _snapshot_info(name: str, version: str = "10.2.0") -> "SnapshotInfo":
    from config_scanner.service import SnapshotInfo

    return SnapshotInfo(
        name=name,
        build_number=None,
        product_version=version,
        source_version=None,
        exe_product_version=version,
        exe_product_name=None,
        branch=None,
        scan_timestamp="",
        file_count=1,
        game_drive="",
        is_baseline=False,
    )


def test_configure_snapshot_table_view_runs_on_qtableview() -> None:
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QHeaderView,
        QStyleOptionViewItem,
        QTableView,
    )

    QApplication.instance() or QApplication([])
    table = QTableView()
    assert not hasattr(table, "setUniformRowHeights")
    configure_snapshot_table_view(table)
    assert table.objectName() == "configScannerSnapshotTable"
    assert table.wordWrap() is False
    assert table.textElideMode() == Qt.TextElideMode.ElideNone
    assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert table.verticalHeader().defaultSectionSize() == SNAPSHOT_TABLE_ROW_PX
    assert (
        table.verticalHeader().sectionResizeMode(0)
        == QHeaderView.ResizeMode.Fixed
    )
    model = SnapshotTableModel()
    model.set_rows(
        [
            _snapshot_info("full", "10.2.0.876"),
            _snapshot_info("short", "10.2.0"),
        ]
    )
    assert bool(model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEnabled)
    assert bool(model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsSelectable)
    table.setModel(model)
    delegate = SnapshotVersionDelegate(table)
    table.setItemDelegateForColumn(SnapshotTableModel.VERSION_COL, delegate)
    pix = QPixmap(120, 24)
    pix.fill()
    painter = QPainter(pix)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 120, 24)
    option.widget = table
    delegate.paint(painter, option, model.index(0, SnapshotTableModel.VERSION_COL))
    delegate.paint(painter, option, model.index(1, SnapshotTableModel.VERSION_COL))
    painter.end()
    assert model.index(0, SnapshotTableModel.VERSION_COL).data() == "10.2.0.876"
    assert model.index(1, SnapshotTableModel.VERSION_COL).data() == "10.2.0"


def test_resize_snapshot_table_columns_fits_long_profile() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QTableView

    from config_scanner.service import SnapshotInfo

    QApplication.instance() or QApplication([])
    table = QTableView()
    configure_snapshot_table_view(table)
    model = SnapshotTableModel()
    long_name = (
        "2026-08-24_GRT330106_Ruleta_Alegro_Wing_v10.1.8.0_b38884_092225"
    )
    model.set_rows(
        [
            SnapshotInfo(
                name=long_name,
                build_number="38884",
                product_version="10.1.8.0",
                source_version="38884",
                exe_product_version="10.1.8.0",
                exe_product_name="Ruleta",
                branch="Ruleta",
                scan_timestamp="2026-08-24T09:22:25",
                file_count=472,
                game_drive="\\\\10.0.0.111\\slot",
                is_baseline=False,
                profile_label="Ruleta Alegro Wing",
                profile_id="roulette_usb",
                has_software=True,
                has_archive=True,
            )
        ]
    )
    table.setModel(model)
    table.resize(400, 120)
    resize_snapshot_table_columns_to_contents(table)
    header = table.horizontalHeader()
    fm = table.fontMetrics()
    profile_text = str(model.data(model.index(0, 1), Qt.DisplayRole) or "")
    assert header.sectionSize(0) >= fm.horizontalAdvance(long_name)
    assert header.sectionSize(1) >= fm.horizontalAdvance(profile_text)


def test_snapshot_table_click_selects_each_row() -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QTableView, QVBoxLayout, QWidget

    from gui.theme import STYLESHEET

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.setStyleSheet(STYLESHEET)
    table = QTableView(host)
    model = SnapshotTableModel()
    model.set_rows(
        [
            _snapshot_info("first", "10.1.8.0"),
            _snapshot_info("second", "10.2.0.876"),
            _snapshot_info("third", "10.2.0.684"),
        ]
    )
    table.setModel(model)
    configure_snapshot_table_view(table)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(table)
    host.resize(720, 240)
    host.show()
    app.processEvents()

    for row in (2, 0, 1):
        rect = table.visualRect(model.index(row, 0))
        assert rect.isValid() and rect.height() > 0
        QTest.mouseClick(
            table.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            rect.center(),
        )
        app.processEvents()
        selected = table.selectionModel().selectedRows()
        assert selected, f"row {row} click selected nothing"
        assert selected[0].row() == row

    miss = QPoint(8, SNAPSHOT_TABLE_ROW_PX * 10)
    assert snapshot_table_row_from_pos(table, miss) == -1
    host.close()
