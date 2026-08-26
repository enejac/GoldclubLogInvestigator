"""Hold the MATCH/MISMATCH verdict until both columns are from one round.

A COM capture takes seconds and a snapshot read milliseconds, so during Auto fetch
the two columns are briefly a few games apart on a machine in play. Rows in that
state read SYNCING instead of flashing MISMATCH.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from gui.sas_verify_dialog import SAS_STATUS_SYNCING, compare_status

_ALIASES = {"0000": "coinin", "0001": "coinout"}


def _fake_vm() -> SimpleNamespace:
    return SimpleNamespace(
        current_product_name="GUI-Test",
        get_gm2u_value_for_sas_code=lambda code, state: state.get(
            _ALIASES.get((code or "").upper(), ""), ""
        ),
    )


def test_compare_status_reports_a_match_whenever_the_values_agree() -> None:
    assert compare_status(match=True, sources_settled=True) == "MATCH"
    assert compare_status(match=True, sources_settled=False) == "MATCH"


def test_compare_status_holds_a_difference_until_both_sides_landed() -> None:
    assert compare_status(match=False, sources_settled=True) == "MISMATCH"
    assert compare_status(match=False, sources_settled=False) == SAS_STATUS_SYNCING


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


def _dialog_with_both_columns():
    """Slot on a local root: Machine from gm2au, SAS from the controller snapshot."""
    from PySide6.QtCore import QThreadPool

    from gui.sas_verify_dialog import SasVerifyDialog

    root = r"G:\Goldclub\var"
    dlg = SasVerifyDialog(_fake_vm(), QThreadPool.globalInstance(), scan_root=root)
    dlg._prefetch_started = True
    dlg._auto_fetch_toggle.setChecked(False)
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
    # coinin differs (a game was played between the two reads); coinout agrees.
    dlg._on_local_diff_done("", {"coinin": "1234", "coinout": "50"})
    return dlg


def test_settled_columns_report_the_real_verdict() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_with_both_columns()

    assert dlg._compare_sources_settled() is True
    statuses = _statuses_by_code(dlg)
    assert statuses["0000"] == "MISMATCH"
    assert statuses["0001"] == "MATCH"

    dlg.deleteLater()
    app.processEvents()


def test_mid_round_difference_reads_syncing_and_asks_for_a_repaint() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_with_both_columns()

    # Capture landed, this round's snapshot re-read has not.
    dlg._auto_fetch_round_active = True
    assert dlg._compare_sources_settled() is False
    dlg._render(parsed_rows=dlg._last_parsed_rows, allow_machine_lookup=True)

    statuses = _statuses_by_code(dlg)
    assert statuses["0000"] == SAS_STATUS_SYNCING
    # Agreeing rows are not held back — nothing to warn about.
    assert statuses["0001"] == "MATCH"
    assert dlg._settle_repaint_timer.isActive()

    # The held verdict is painted once the round closes, without a new fetch.
    dlg._auto_fetch_round_active = False
    dlg._on_settle_repaint()
    assert _statuses_by_code(dlg)["0000"] == "MISMATCH"

    dlg.deleteLater()
    app.processEvents()


def test_a_running_capture_holds_the_verdict() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_with_both_columns()

    dlg._meter_fetch_running = lambda: True  # type: ignore[method-assign]
    assert dlg._compare_sources_settled() is False
    dlg._meter_fetch_running = lambda: False  # type: ignore[method-assign]
    dlg._local_diff_running = True
    assert dlg._compare_sources_settled() is False

    dlg.deleteLater()
    app.processEvents()


def test_auto_fetch_rereads_the_snapshot_after_the_capture() -> None:
    """The slow side finishes last, so the fast side is read again to match it."""
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
    dlg._reload_game_theme_catalog = lambda: None  # type: ignore[method-assign]
    blocked = dlg._auto_fetch_toggle.blockSignals(True)
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._auto_fetch_toggle.blockSignals(blocked)

    compares: list[dict] = []
    dlg._begin_cabinet_compare = lambda **kw: compares.append(kw)  # type: ignore[method-assign]

    dlg._auto_fetch_round_active = True
    dlg._resync_machine_after_auto_fetch_capture()
    assert dlg._auto_fetch_round_resynced is True
    assert dlg._compare_sources_settled() is False
    app.processEvents()  # the re-read is queued, never run inline
    assert compares and compares[0]["force"] is True
    assert compares[0]["immediate_paint"] is False

    # The re-read landing ends the round, so verdicts may show again.
    dlg._apply_cabinet_state({"coinin": "1000"})
    assert dlg._auto_fetch_round_active is False
    assert dlg._compare_sources_settled() is True

    dlg.deleteLater()
    app.processEvents()


def test_a_round_with_nothing_to_pair_with_closes_at_once() -> None:
    """No second read is coming (Auto fetch off), so stop holding the verdicts."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_with_both_columns()

    dlg._auto_fetch_round_active = True
    dlg._resync_machine_after_auto_fetch_capture()

    assert dlg._auto_fetch_round_active is False
    assert dlg._auto_fetch_round_resynced is False
    assert dlg._compare_sources_settled() is True

    dlg.deleteLater()
    app.processEvents()


def _remote_dialog():
    """Auto fetch against a cabinet share, where SAS and Machine land apart."""
    from PySide6.QtCore import QThreadPool

    from gui.sas_verify_dialog import SasVerifyDialog

    dlg = SasVerifyDialog(
        _fake_vm(), QThreadPool.globalInstance(), scan_root=r"\\10.0.0.90\c$\Goldclub\var"
    )
    dlg._prefetch_started = True
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    dlg._reload_game_theme_catalog = lambda: None  # type: ignore[method-assign]
    dlg._schedule_game_theme_catalog_reload = lambda: None  # type: ignore[method-assign]
    return dlg


def test_machine_paint_waits_only_for_a_capture_inside_a_round() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _remote_dialog()

    # A manual capture has no round to pair with — paint the moment XML lands.
    dlg._meter_fetch_running = lambda: True  # type: ignore[method-assign]
    dlg._auto_fetch_round_active = False
    assert dlg._machine_paint_waits_for_sas() is False

    dlg._auto_fetch_round_active = True
    assert dlg._machine_paint_waits_for_sas() is True

    # Nothing left to pair with.
    dlg._meter_fetch_running = lambda: False  # type: ignore[method-assign]
    assert dlg._machine_paint_waits_for_sas() is False

    dlg.deleteLater()
    app.processEvents()


def test_one_game_moves_both_columns_in_a_single_paint() -> None:
    """Machine reads in ~40 ms and SAS takes seconds — do not paint them apart."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _remote_dialog()

    painted: list[bool] = []
    dlg._run_cabinet_ui_refresh = lambda: painted.append(True)  # type: ignore[method-assign]

    dlg._auto_fetch_round_active = True
    dlg._meter_fetch_running = lambda: True  # type: ignore[method-assign]
    dlg._apply_cabinet_state({"coinin": "1200", "coinout": "50"})

    # Values are in memory, but the table has not jumped ahead of SAS.
    assert painted == []
    assert dlg._cabinet_ui_refresh_pending is True
    assert dlg._machine_state["coinin"] == "1200"

    # The capture lands: one repaint brings both columns up together.
    dlg._meter_fetch_running = lambda: False  # type: ignore[method-assign]
    dlg._flush_pending_cabinet_ui_refresh()
    assert painted == [True]

    dlg.deleteLater()
    app.processEvents()


def test_a_deferred_machine_paint_is_never_stranded_by_a_failed_capture() -> None:
    """The error path can fire while the COM thread is still winding down."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _remote_dialog()

    painted: list[bool] = []
    dlg._run_cabinet_ui_refresh = lambda: painted.append(True)  # type: ignore[method-assign]
    dlg._cabinet_ui_refresh_pending = True

    dlg._on_meter_fetch_thread_finished()
    assert painted == [True]

    dlg.deleteLater()
    app.processEvents()


def test_pre_com_machine_landing_stays_syncing_while_resync_is_queued() -> None:
    """COM finished first; deferred Machine re-read must not flash MISMATCH."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        _fake_vm(), QThreadPool.globalInstance(), scan_root=r"\\10.0.0.90\c$\Goldclub\var"
    )
    dlg._prefetch_started = True
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]
    dlg._reload_game_theme_catalog = lambda: None  # type: ignore[method-assign]
    dlg._schedule_game_theme_catalog_reload = lambda: None  # type: ignore[method-assign]
    blocked = dlg._auto_fetch_toggle.blockSignals(True)
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._auto_fetch_toggle.blockSignals(blocked)

    dlg._auto_fetch_round_active = True
    dlg._auto_fetch_round_resynced = True
    dlg._cabinet_compare_force_pending = True
    dlg._meter_fetch_running = lambda: False  # type: ignore[method-assign]
    dlg._compare_running = lambda: False  # type: ignore[method-assign]

    # Stale Machine (pre-COM) lands while the paired re-read is still queued.
    dlg._apply_cabinet_state({"coinin": "1000", "coinout": "50"})
    assert dlg._auto_fetch_round_active is True
    assert dlg._compare_sources_settled() is False

    dlg.deleteLater()
    app.processEvents()


def test_local_diff_payload_pairs_machine_and_sas_from_one_read() -> None:
    """Local EGM: never settle Machine from CompareWorker + SAS from a later read."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_with_both_columns()
    dlg._auto_fetch_round_active = True
    # Stale Machine left behind by CompareWorker (one credit behind).
    dlg._machine_state = {"coinin": "1000", "coinout": "50"}

    dlg._on_local_diff_done(
        "",
        {
            "sas": {"coinin": "1234", "coinout": "50"},
            "machine": {"coinin": "1234", "coinout": "50"},
        },
    )

    assert dlg._machine_state["coinin"] == "1234"
    assert dlg._local_sas_state["coinin"] == "1234"
    assert dlg._auto_fetch_round_active is False
    assert dlg._compare_sources_settled() is True
    assert _statuses_by_code(dlg)["0000"] == "MATCH"

    dlg.deleteLater()
    app.processEvents()


def test_force_pending_or_compare_running_holds_verdict() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_with_both_columns()

    dlg._cabinet_compare_force_pending = True
    assert dlg._compare_sources_settled() is False
    dlg._cabinet_compare_force_pending = False
    dlg._compare_running = lambda: True  # type: ignore[method-assign]
    assert dlg._compare_sources_settled() is False
    dlg._compare_running = lambda: False  # type: ignore[method-assign]
    dlg._auto_fetch_round_resynced = True
    assert dlg._compare_sources_settled() is False

    dlg.deleteLater()
    app.processEvents()


def test_orphaned_meter_fetch_finished_does_not_clear_newer_thread() -> None:
    """An older finished sender must not null a newer capture's refs."""
    from PySide6.QtCore import QObject, QThread
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _remote_dialog()

    class _FakeSender(QObject):
        pass

    newer = QThread(dlg)
    dlg._meter_fetch_thread = newer
    dlg._meter_fetch_worker = object()  # type: ignore[assignment]
    # Simulate an orphaned finished with a different sender.
    orphan = _FakeSender()
    dlg.sender = lambda: orphan  # type: ignore[method-assign]
    dlg._on_meter_fetch_thread_finished()
    assert dlg._meter_fetch_thread is newer

    # Matching sender clears normally.
    dlg.sender = lambda: newer  # type: ignore[method-assign]
    dlg._on_meter_fetch_thread_finished()
    assert dlg._meter_fetch_thread is None

    newer.deleteLater()
    dlg.deleteLater()
    app.processEvents()


def test_settle_repaint_skips_when_signals_disabled() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_with_both_columns()
    renders: list[bool] = []
    dlg._render = lambda **kw: renders.append(True)  # type: ignore[method-assign]
    dlg._accept_worker_signals = False
    dlg._on_settle_repaint()
    assert renders == []

    dlg.deleteLater()
    app.processEvents()


def test_ram_clear_busy_apply_clears_force_pending_and_round() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _remote_dialog()
    dlg._active_compare_job_id = 1
    dlg._compare_job_roots[1] = r"\\10.0.0.90\c$\Goldclub\var"
    dlg._ram_clear_busy = True
    dlg._cabinet_compare_force_pending = True
    dlg._manual_meters_refresh = True
    dlg._auto_fetch_round_active = True
    dlg._auto_fetch_round_started_mono = 1.0

    dlg._apply_cabinet_state({"coinin": "1"}, job_id=1)
    assert dlg._cabinet_compare_force_pending is False
    assert dlg._manual_meters_refresh is False
    assert dlg._auto_fetch_round_active is False

    dlg.deleteLater()
    app.processEvents()


def test_stop_psexec_uses_orphan_helper() -> None:
    from PySide6.QtCore import QObject, QThread
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _remote_dialog()
    orphaned: list[object] = []

    class _FakeThread(QThread):
        def isRunning(self) -> bool:  # type: ignore[override]
            return True

        def quit(self) -> None:  # type: ignore[override]
            return None

        def wait(self, *_a) -> bool:  # type: ignore[override]
            return False

        def setParent(self, parent) -> None:  # type: ignore[override]
            orphaned.append(parent)

    th = _FakeThread(dlg)
    dlg._psexec_verify_thread = th
    dlg._psexec_verify_worker = object()  # type: ignore[assignment]
    dlg._stop_psexec_verify_thread(wait_ms=1)
    assert dlg._psexec_verify_thread is None
    assert dlg._psexec_verify_worker is None
    assert None in orphaned  # setParent(None) from orphan helper

    # Orphan finished must not clear a newer thread.
    newer = QThread(dlg)
    dlg._psexec_verify_thread = newer
    orphan = QObject()
    dlg.sender = lambda: orphan  # type: ignore[method-assign]
    dlg._on_psexec_verify_thread_finished()
    assert dlg._psexec_verify_thread is newer

    newer.deleteLater()
    th.deleteLater()
    dlg.deleteLater()
    app.processEvents()
