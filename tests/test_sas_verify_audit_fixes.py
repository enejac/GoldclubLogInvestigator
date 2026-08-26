"""Focused tests for SAS Verify audit fixes (local SAS gate, RAM Clear, mismatch)."""

from __future__ import annotations

from types import SimpleNamespace

from gui.sas_verify_dialog import SAS_STATUS_SYNCING, Sas6FRow, compare_status


_ALIASES = {"0000": "coinin", "0001": "coinout"}


def _fake_vm() -> SimpleNamespace:
    return SimpleNamespace(
        current_product_name="GUI-Test",
        get_gm2u_value_for_sas_code=lambda code, state: state.get(
            _ALIASES.get((code or "").upper(), ""), ""
        ),
    )


def _make_dlg(scan_root: str = r"G:\Goldclub\var"):
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(_fake_vm(), QThreadPool.globalInstance(), scan_root=scan_root)
    dlg._prefetch_started = True
    return app, dlg


def test_local_sas_fallback_gated_to_share_meters_mode() -> None:
    """Stale _local_sas_state must not fill SAS when COM capture is the source."""
    app, dlg = _make_dlg(r"\\10.0.0.90\c$\Goldclub\var")
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]
    dlg._cabinet_share_only_mode = False
    dlg._local_sas_state = {"coinin": "9999"}
    dlg._last_parsed_rows = [Sas6FRow(meter_id="0000", sas_value_text="")]

    assert dlg._share_meters_without_com() is False
    assert dlg._sas_value_for_code("0000") == ""

    dlg._cabinet_share_only_mode = True
    assert dlg._share_meters_without_com() is True
    assert dlg._sas_value_for_code("0000") == "9999"

    dlg.deleteLater()
    app.processEvents()


def test_local_sas_cleared_on_clear_all() -> None:
    app, dlg = _make_dlg()
    dlg._local_files_only_mode = lambda: True  # type: ignore[method-assign]
    dlg._local_sas_state = {"coinin": "1234"}
    dlg._had_settled_mismatch = True

    dlg._clear_all()

    assert dlg._local_sas_state == {}
    assert dlg._had_settled_mismatch is False

    dlg.deleteLater()
    app.processEvents()


def test_ram_clear_button_disabled_without_tip_on_non_egm(monkeypatch) -> None:
    app, dlg = _make_dlg(r"C:\Users\me\usb_export\logs")
    monkeypatch.setattr(
        "network.health_monitor.is_running_on_local_egm", lambda: False
    )
    dlg._ram_clear_target_ip = lambda: ""  # type: ignore[method-assign]
    dlg._ram_clear_allowed_on_local_egm = lambda: False  # type: ignore[method-assign]
    dlg._ram_clear_busy = False

    assert hasattr(dlg, "_act_ram_clear")
    dlg._update_ram_clear_button_enabled()
    assert dlg._act_ram_clear.isEnabled() is False

    dlg._ram_clear_target_ip = lambda: "10.0.0.90"  # type: ignore[method-assign]
    dlg._update_ram_clear_button_enabled()
    assert dlg._act_ram_clear.isEnabled() is True

    dlg.deleteLater()
    app.processEvents()


def test_mismatch_detected_syncing_vs_settled_mismatch() -> None:
    """SYNCING must not set the sticky flag; MISMATCH must."""
    app, dlg = _make_dlg()
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]
    dlg._compare_sources_settled = lambda: False  # type: ignore[method-assign]
    dlg._machine_state = {"coinin": "100"}
    dlg._machine_state_loaded = True
    dlg._had_settled_mismatch = False

    rows = [Sas6FRow(meter_id="0000", sas_value_text="200")]
    dlg._last_parsed_rows = rows
    dlg._render(parsed_rows=rows, allow_machine_lookup=True)
    app.processEvents()

    assert compare_status(match=False, sources_settled=False) == SAS_STATUS_SYNCING
    assert dlg._had_settled_mismatch is False
    assert dlg.mismatch_detected() is False

    dlg._compare_sources_settled = lambda: True  # type: ignore[method-assign]
    dlg._render(parsed_rows=rows, allow_machine_lookup=True)
    app.processEvents()

    assert dlg._had_settled_mismatch is True
    assert dlg.mismatch_detected() is True

    dlg._blank_meter_ui_for_ram_clear(status="cleared")
    assert dlg._had_settled_mismatch is False
    assert dlg._local_sas_state == {}

    dlg.deleteLater()
    app.processEvents()


def test_blank_meter_invalidates_meter_fetch_and_local_sas() -> None:
    from gui.sas_verify_dialog import SasVerifyDialog

    dlg = SasVerifyDialog.__new__(SasVerifyDialog)
    dlg._discard_stale_machine_after_ram_clear = False
    dlg._machine_empty_retry_armed = False
    dlg._machine_empty_retry_count = 0
    dlg._cabinet_compare_force_pending = False
    dlg._pending_forced_fetch = None
    dlg._compare_ui_pending = False
    dlg._meters_ui_pending = False
    dlg._manual_meters_refresh = False
    dlg._local_diff_running = False
    dlg._auto_fetch_round_active = False
    dlg._auto_fetch_round_resynced = False
    dlg._auto_fetch_round_started_mono = 0.0
    dlg._compare_job_id = 1
    dlg._active_compare_job_id = 1
    dlg._local_diff_job_id = 1
    dlg._meter_fetch_job_id = 3
    dlg._active_meter_fetch_job_id = 3
    dlg._local_sas_state = {"coinin": "9"}
    dlg._had_settled_mismatch = True
    dlg._machine_state = {}
    dlg._machine_state_loaded = False
    dlg._machine_state_loaded_at = 0.0
    dlg._loaded_cabinet_scan_root = ""
    dlg._cached_meter_result = object()
    dlg._sas_2f_values = {}
    dlg._last_parsed_rows = []
    dlg._last_bill_in_rows = []
    dlg._last_bill_out_rows = []
    dlg._meter_fetch_error = None
    dlg._meter_fetch_user_clicked_apply = False
    dlg._last_displayed_paste_fingerprint = ""

    class _Btn:
        def setEnabled(self, *_a, **_k):
            pass

        def setText(self, *_a, **_k):
            pass

    class _Ui:
        compare_btn = _Btn()

    dlg.ui = _Ui()
    dlg._btn_get_meters = _Btn()
    dlg._fetched_status_label = type("L", (), {"setText": lambda self, t: None})()
    dlg._invalidate_machine_cabinet_cache = lambda **_k: None
    dlg._set_busy_progress_active = lambda *_a, **_k: None
    dlg._set_prefetch_status_text = lambda *_a, **_k: None
    dlg._render = lambda **_k: None
    dlg._render_bills = lambda **_k: None
    dlg._render_coins = lambda **_k: None
    dlg._reset_game_summary = lambda: None
    dlg._reset_master_summary = lambda: None
    dlg._reset_transfer_summary = lambda: None
    dlg._reset_security_summary = lambda: None
    stopped = {"n": 0}

    def _stop(**_k):
        stopped["n"] += 1
        return True

    dlg._stop_meter_fetch_thread = _stop  # type: ignore[method-assign]

    SasVerifyDialog._blank_meter_ui_for_ram_clear(dlg, status="cleared")
    assert dlg._local_sas_state == {}
    assert dlg._had_settled_mismatch is False
    assert dlg._meter_fetch_job_id == 4
    assert dlg._active_meter_fetch_job_id == 4
    assert stopped["n"] == 1
