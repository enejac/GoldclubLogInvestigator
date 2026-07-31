"""Critical SasVerify Compare busy-release / scan-root edge cases."""

from gui.sas_verify_dialog import SasVerifyDialog


class _Btn:
    def __init__(self) -> None:
        self.enabled = True
        self.text = "Compare"

    def setEnabled(self, value: bool) -> None:
        self.enabled = bool(value)

    def setText(self, value: str) -> None:
        self.text = str(value)


class _Ui:
    def __init__(self) -> None:
        self.compare_btn = _Btn()


def _bare_dialog() -> SasVerifyDialog:
    dlg = SasVerifyDialog.__new__(SasVerifyDialog)
    dlg.ui = _Ui()
    dlg._btn_get_meters = _Btn()
    dlg._cabinet_compare_prefetch = True
    dlg._compare_ui_pending = True
    dlg._ram_clear_busy = False
    dlg._compare_thread = None
    dlg._set_busy_progress_active = lambda *_a, **_k: None
    return dlg


def test_release_compare_busy_ui_restores_button() -> None:
    dlg = _bare_dialog()
    dlg.ui.compare_btn.setEnabled(False)
    dlg.ui.compare_btn.setText("Scanning Cabinet...")
    SasVerifyDialog._release_compare_busy_ui(dlg, reason="unit")
    assert dlg.ui.compare_btn.enabled is True
    assert dlg.ui.compare_btn.text == "Compare"
    assert dlg._compare_ui_pending is False
    assert dlg._cabinet_compare_prefetch is False


def test_release_if_idle_skips_when_compare_running() -> None:
    dlg = _bare_dialog()
    dlg.ui.compare_btn.setEnabled(False)
    dlg.ui.compare_btn.setText("Scanning Cabinet...")

    class _Running:
        def isRunning(self) -> bool:
            return True

    dlg._compare_thread = _Running()
    SasVerifyDialog._release_compare_busy_ui_if_idle(dlg, reason="stale_job")
    assert dlg.ui.compare_btn.text == "Scanning Cabinet..."
    assert dlg.ui.compare_btn.enabled is False


def test_release_if_idle_skips_during_ram_clear_busy() -> None:
    dlg = _bare_dialog()
    dlg.ui.compare_btn.setEnabled(False)
    dlg.ui.compare_btn.setText("Scanning Cabinet...")
    dlg._ram_clear_busy = True
    SasVerifyDialog._release_compare_busy_ui_if_idle(dlg, reason="stale_job")
    assert dlg.ui.compare_btn.text == "Scanning Cabinet..."


def test_stale_finish_releases_when_idle() -> None:
    """RAM-clear blank bumps job id; orphaned finish must unlock Compare."""
    dlg = _bare_dialog()
    dlg.ui.compare_btn.setEnabled(False)
    dlg.ui.compare_btn.setText("Scanning Cabinet...")
    dlg._active_compare_job_id = 6
    dlg._worker_signals_enabled = lambda: True

    calls: list[str] = []

    def _trace(*_a, **_k):
        return None

    import gui.sas_verify_dialog as svd

    orig = svd.trace_event
    svd.trace_event = _trace  # type: ignore[assignment]
    try:
        SasVerifyDialog._on_worker_finished(dlg, {}, job_id=5)
    finally:
        svd.trace_event = orig  # type: ignore[assignment]
    assert dlg.ui.compare_btn.text == "Compare"
    assert dlg.ui.compare_btn.enabled is True


def test_keep_machine_false_after_ram_clear_discard() -> None:
    dlg = SasVerifyDialog.__new__(SasVerifyDialog)
    dlg._discard_stale_machine_after_ram_clear = True
    dlg._ram_clear_busy = False
    dlg._auto_fetch_toggle = type("T", (), {"isChecked": lambda self: True})()
    dlg._machine_state = {"gameplayedcnt": "9"}
    dlg._loaded_cabinet_scan_root = r"C:\Goldclub\var"
    assert SasVerifyDialog._keep_machine_on_empty_reload(dlg) is False
