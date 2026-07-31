"""RAM Clear completion dialog must stay short and treat finished clears as success."""

from gui.ram_clear_ui import format_ram_clear_finished_dialog
from network.ram_clear import summarize_ram_clear_output


NOISY = """
VERBOSE: Exporting function 'Get-PhysicalDisk'.
VERBOSE: Exporting alias 'Get-PhysicalDiskSNV'.
slot
Goldclub root: C:\\Goldclub
Clearing: C:\\Goldclub\\var\\state\\OneHand
[END] ensure state wipe (official targets only)
[START] LogDaemonRamClear (soft meters / SAS 0x7A)
Running: C:\\Goldclub\\bin\\LogDaemonRamClear.exe
LogDaemonRamClear exit=0
[END] LogDaemonRamClear
[START] post-start services
[END] post-start services
[START] post-start game
Starting game: C:\\Goldclub/slot/OneHand.exe
[END] post-start game
[END] LogInvestigator RAM Clear
"""


def test_summarize_strips_storage_verbose_and_succeeds() -> None:
    ok, msg = summarize_ram_clear_output(NOISY, returncode=1)
    assert ok is True
    assert "Exporting" not in msg
    assert "VERBOSE" not in msg
    assert "RAM Clear completed" in msg
    assert "LogDaemonRamClear" in msg
    assert len(msg) < 600


def test_finished_dialog_uses_short_body_and_info_path() -> None:
    # Worker often reports ok=False because PowerShell exit != 0 despite success.
    display_ok, body = format_ram_clear_finished_dialog(
        ok=False, msg=NOISY, label="slot"
    )
    assert display_ok is True
    assert body.startswith("slot")
    assert "Exporting" not in body
    assert "RAM Clear completed" in body


def test_finished_dialog_keeps_real_failures_short() -> None:
    raw = "VERBOSE: Exporting function X\n[ERROR] 10-Backup.ps1: boom\n"
    display_ok, body = format_ram_clear_finished_dialog(ok=False, msg=raw, label="slot")
    assert display_ok is False
    assert "Exporting" not in body
    assert "[ERROR]" in body


FAST_SUCCESS = """
[END] ensure state wipe (official targets only)
[START] post-start services
[END] post-start services
[START] post-start game
Starting game: C:\\Goldclub\\slot\\OneHand.exe
[END] post-start game
[MILESTONE] game up - soft-meter stamp (0x7A) continuing in background
[END] LogInvestigator RAM Clear
"""


def test_summarize_fast_path_background_stamp() -> None:
    ok, msg = summarize_ram_clear_output(FAST_SUCCESS, returncode=0)
    assert ok is True
    assert "RAM Clear completed" in msg
    assert "background" in msg.lower()
    assert "Game restarted" in msg


def test_sas_verify_finished_schedules_meter_refresh() -> None:
    import inspect

    from gui import sas_verify_dialog as svd

    src = inspect.getsource(svd.SasVerifyDialog._on_ram_clear_finished)
    assert "_schedule_post_ram_clear_meter_refresh" in src
    sched = inspect.getsource(svd.SasVerifyDialog._schedule_post_ram_clear_meter_refresh)
    assert "_on_get_meters_clicked" in sched
    assert "_blank_meter_ui_for_ram_clear" in sched
    click = inspect.getsource(svd.SasVerifyDialog._on_ram_clear_clicked)
    assert "_blank_meter_ui_for_ram_clear" in click
    keep = inspect.getsource(svd.SasVerifyDialog._keep_machine_on_empty_reload)
    assert "_discard_stale_machine_after_ram_clear" in keep


def test_blank_meter_ui_for_ram_clear_clears_state() -> None:
    from network.sas_serial_meters import DEFAULT_6F_VERIFY_POLL_CODES
    from gui.sas_verify_dialog import Sas6FRow, SasVerifyDialog

    dlg = SasVerifyDialog.__new__(SasVerifyDialog)
    dlg._discard_stale_machine_after_ram_clear = False
    dlg._machine_empty_retry_armed = True
    dlg._machine_empty_retry_count = 3
    dlg._cabinet_compare_force_pending = True
    dlg._pending_forced_fetch = {"prefetch": True}
    dlg._compare_ui_pending = True
    dlg._meters_ui_pending = True
    dlg._manual_meters_refresh = True
    dlg._local_diff_running = True
    dlg._auto_fetch_round_active = True
    dlg._auto_fetch_round_resynced = True
    dlg._auto_fetch_round_started_mono = 1.0
    dlg._compare_job_id = 4
    dlg._active_compare_job_id = 4
    dlg._local_diff_job_id = 2
    dlg._machine_state = {"gameplayedcnt": "6"}
    dlg._machine_state_loaded = True
    dlg._machine_state_loaded_at = 1.0
    dlg._loaded_cabinet_scan_root = r"C:\Goldclub\var\log"
    dlg._cached_meter_result = object()
    dlg._sas_2f_values = {"0005": "6"}
    dlg._last_parsed_rows = [Sas6FRow(meter_id="0005", sas_value_text="6")]
    dlg._last_bill_in_rows = [1]
    dlg._last_bill_out_rows = [1]

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

    SasVerifyDialog._blank_meter_ui_for_ram_clear(
        dlg, status="cleared"
    )
    assert dlg._discard_stale_machine_after_ram_clear is True
    assert dlg._cached_meter_result is None
    assert dlg._sas_2f_values == {}
    assert dlg._last_bill_in_rows == []
    assert dlg._compare_ui_pending is False
    assert dlg._auto_fetch_round_active is False
    assert dlg._active_compare_job_id == 5
    assert all(not (r.sas_value_text or "").strip() for r in dlg._last_parsed_rows)
    assert len(dlg._last_parsed_rows) == len(DEFAULT_6F_VERIFY_POLL_CODES)
    assert SasVerifyDialog._keep_machine_on_empty_reload(dlg) is False