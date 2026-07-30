"""Auto-fetch toggle: EGM state-file watcher and fetched-at status message."""

from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace

from gui.sas_verify_dialog import (
    AUTO_FETCH_BUSY_RETRY_S,
    AUTO_FETCH_MIN_REFRESH_S,
    auto_fetch_refresh_delay_s,
    evaluate_auto_fetch_poll,
    meters_fetched_status_text,
)


def test_evaluate_auto_fetch_poll_failure_changes_nothing() -> None:
    assert evaluate_auto_fetch_poll(0.0, 0.0) == (0.0, False)
    assert evaluate_auto_fetch_poll(100.0, 0.0) == (100.0, False)
    assert evaluate_auto_fetch_poll(100.0, -1.0) == (100.0, False)


def test_evaluate_auto_fetch_poll_first_poll_sets_baseline_without_fetch() -> None:
    assert evaluate_auto_fetch_poll(0.0, 123.5) == (123.5, False)


def test_evaluate_auto_fetch_poll_unchanged_and_older_do_not_fetch() -> None:
    assert evaluate_auto_fetch_poll(123.5, 123.5) == (123.5, False)
    assert evaluate_auto_fetch_poll(123.5, 100.0) == (123.5, False)


def test_evaluate_auto_fetch_poll_newer_mtime_triggers_fetch() -> None:
    assert evaluate_auto_fetch_poll(123.5, 200.0) == (200.0, True)


def test_auto_fetch_refresh_delay_first_refresh_runs_now() -> None:
    assert (
        auto_fetch_refresh_delay_s(now_mono=500.0, last_refresh_mono=0.0, busy=False) == 0.0
    )


def test_auto_fetch_refresh_delay_spaces_out_bursts() -> None:
    # 2 s after the last refresh: wait out the rest of the spacing window.
    delay = auto_fetch_refresh_delay_s(now_mono=502.0, last_refresh_mono=500.0, busy=False)
    assert delay == AUTO_FETCH_MIN_REFRESH_S - 2.0
    # Window already elapsed: run immediately.
    assert (
        auto_fetch_refresh_delay_s(
            now_mono=500.0 + AUTO_FETCH_MIN_REFRESH_S, last_refresh_mono=500.0, busy=False
        )
        == 0.0
    )
    # Local EGM uses a tighter coalesce window.
    from gui.sas_verify_dialog import AUTO_FETCH_MIN_REFRESH_LOCAL_S

    delay_local = auto_fetch_refresh_delay_s(
        now_mono=500.2,
        last_refresh_mono=500.0,
        busy=False,
        min_refresh_s=AUTO_FETCH_MIN_REFRESH_LOCAL_S,
    )
    assert abs(delay_local - (AUTO_FETCH_MIN_REFRESH_LOCAL_S - 0.2)) < 1e-9


def test_auto_fetch_refresh_delay_waits_while_work_is_in_flight() -> None:
    # Never start on top of a live compare / COM capture, even when due.
    assert (
        auto_fetch_refresh_delay_s(now_mono=900.0, last_refresh_mono=0.0, busy=True)
        == AUTO_FETCH_BUSY_RETRY_S
    )
    # The longer of the two brakes wins.
    assert auto_fetch_refresh_delay_s(
        now_mono=501.0, last_refresh_mono=500.0, busy=True
    ) == AUTO_FETCH_MIN_REFRESH_S - 1.0


def test_meters_fetched_status_text_second_accurate() -> None:
    dt = datetime(2026, 7, 28, 15, 23, 41)
    assert meters_fetched_status_text(dt) == "Meters fetched 2026-07-28 15:23:41"


def test_meter_flash_strength_pulses_then_fades() -> None:
    from gui.sas_verify_dialog import meter_flash_background, meter_flash_strength

    assert meter_flash_strength(-0.1) == 0.0
    assert meter_flash_strength(0.0) > 0.0
    mid = meter_flash_strength(0.35)
    assert 0.0 < mid <= 1.0
    assert meter_flash_strength(9.0) == 0.0
    bg = meter_flash_background(0.8)
    assert bg is not None
    assert bg.alpha() > 0
    assert bg.alpha() < 255  # translucent — must not obscure text
    assert meter_flash_background(0.0) is None


def test_auto_fetch_clears_stale_mtime_poll_latch() -> None:
    """A wedged SMB mtime poll must not block Auto fetch forever."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import AUTO_FETCH_POLL_STALE_S, SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"C:\Goldclub\var\log",
    )
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._auto_fetch_poll_running = True
    dlg._auto_fetch_poll_started_mono = __import__("time").monotonic() - (
        AUTO_FETCH_POLL_STALE_S + 1.0
    )
    dlg._compare_running = lambda: False  # type: ignore[method-assign]
    dlg._auto_fetch_uses_local_disk = lambda: False  # type: ignore[method-assign]
    started: list[str] = []

    class _FakePool:
        def start(self, task) -> None:
            started.append(getattr(task, "_scan_root", ""))

    dlg._pool = _FakePool()  # type: ignore[assignment]
    dlg._on_auto_fetch_timer()
    assert dlg._auto_fetch_poll_running is True  # new poll started
    assert started == [r"C:\Goldclub\var\log"]
    dlg.deleteLater()
    app.processEvents()


def test_auto_fetch_ends_stale_round() -> None:
    """An Auto-fetch round that never settles must clear SYNCING after the watchdog."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import AUTO_FETCH_ROUND_STALE_S, SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"C:\Goldclub\var\log",
    )
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._auto_fetch_round_active = True
    dlg._auto_fetch_round_started_mono = __import__("time").monotonic() - (
        AUTO_FETCH_ROUND_STALE_S + 1.0
    )
    dlg._compare_running = lambda: True  # type: ignore[method-assign]
    dlg._compare_started_mono = __import__("time").monotonic()
    dlg._on_auto_fetch_timer()
    assert dlg._auto_fetch_round_active is False
    dlg.deleteLater()
    app.processEvents()


def test_latest_device_state_mtime_unresolvable_roots() -> None:
    from network.accounting_state_loader import latest_device_state_mtime

    assert latest_device_state_mtime("") == 0.0
    assert latest_device_state_mtime("   ") == 0.0


def test_latest_device_state_mtime_reads_newest(tmp_path, monkeypatch) -> None:
    import os

    import network.goldclub_paths as gp
    from network.accounting_state_loader import latest_device_state_mtime

    state = tmp_path / "state"
    (state / "SASControler1").mkdir(parents=True)
    (state / "gm2au").mkdir(parents=True)
    older = state / "SASControler1" / "DeviceManagerData.xml_1"
    newer = state / "gm2au" / "DeviceManagerData.xml_2"
    older.write_text("<x/>", encoding="utf-8")
    newer.write_text("<x/>", encoding="utf-8")
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))

    layout = SimpleNamespace(state_gcmessenger=state)
    monkeypatch.setattr(gp, "resolve_goldclub_layout", lambda root: layout)

    assert latest_device_state_mtime(r"C:\whatever\Goldclub\var") == 2_000_000



def test_unchecking_auto_fetch_parks_the_busy_line_immediately() -> None:
    """Turning Auto fetch off must stop the blue line now, not after debounce."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root="",
    )
    dlg._prefetch_started = True
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]
    dlg._begin_cabinet_compare = lambda **kw: None  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: True  # type: ignore[method-assign]

    dlg._auto_fetch_toggle.setChecked(True)
    dlg._set_busy_progress_active(True)
    assert dlg._busy_progress.maximum() == 0

    # Pretend a prefetch capture is still in flight when the user cancels.
    dlg._meter_fetch_running = lambda: True  # type: ignore[method-assign]
    dlg._auto_fetch_toggle.setChecked(False)

    assert dlg._busy_progress.maximum() == 1
    assert dlg._busy_progress.value() == 0
    assert not dlg._busy_idle_timer.isActive()
    assert dlg._busy_progress_suppressed is True
    # Leftover workers must not restart the line.
    dlg._set_busy_progress_active(True)
    assert dlg._busy_progress.maximum() == 1
    # Manual Compare is allowed to animate again.
    dlg._busy_progress_suppressed = False
    dlg._set_busy_progress_active(True)
    assert dlg._busy_progress.maximum() == 0

    dlg.deleteLater()
    app.processEvents()


def test_dialog_busy_line_stays_up_between_phases() -> None:
    """Idle is debounced: cabinet load -> COM capture animates as one run."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(SimpleNamespace(current_product_name="GUI-Test"), QThreadPool.globalInstance(), scan_root="")
    dlg._prefetch_started = True

    dlg._set_busy_progress_active(True)
    assert dlg._busy_progress.maximum() == 0

    # One phase finished: the bar keeps animating until the debounce expires.
    dlg._set_busy_progress_active(False)
    assert dlg._busy_progress.maximum() == 0
    assert dlg._busy_idle_timer.isActive()

    # Next phase started inside the window: cancel the pending stop.
    dlg._set_busy_progress_active(True)
    assert not dlg._busy_idle_timer.isActive()
    assert dlg._busy_progress.maximum() == 0

    # Nothing left in flight -> the debounced stop parks the bar.
    dlg._set_busy_progress_active(False)
    dlg._apply_busy_progress_idle()
    assert dlg._busy_progress.maximum() == 1
    assert dlg._busy_progress.value() == 0

    # Work restarted before the timer fired: the stop is dropped.
    dlg._set_busy_progress_active(True)
    dlg._set_busy_progress_active(False)
    dlg._busy_progress_should_run = lambda: True  # type: ignore[method-assign]
    dlg._apply_busy_progress_idle()
    assert dlg._busy_progress.maximum() == 0

    dlg.deleteLater()
    app.processEvents()


def test_dialog_auto_fetch_toggle_behavior() -> None:
    """Headless Qt: unchecked default, timer lifecycle, refetch on newer mtime."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    vm = SimpleNamespace(current_product_name="GUI-Test")
    dlg = SasVerifyDialog(vm, QThreadPool.globalInstance(), scan_root="")
    dlg._prefetch_started = True  # avoid COM/cabinet prefetch side effects in CI
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]

    assert not dlg._auto_fetch_toggle.isChecked()
    assert not dlg._auto_fetch_timer.isActive()

    enable_calls: list[tuple] = []
    dlg._invalidate_machine_cabinet_cache = lambda: enable_calls.append(("invalidate",))  # type: ignore[method-assign]
    dlg._begin_cabinet_compare = lambda **kw: enable_calls.append(("compare", kw))  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: enable_calls.append(("meters", kw)) or True  # type: ignore[method-assign]

    dlg._auto_fetch_toggle.setChecked(True)
    assert dlg._auto_fetch_timer.isActive()
    # The refresh is queued, never run inside the toggled handler (starting a
    # cabinet load / COM capture there re-enters the dialog and freezes it).
    assert dlg._auto_fetch_refresh_queued
    assert enable_calls == []
    dlg._run_queued_auto_fetch_refresh()
    assert not dlg._auto_fetch_refresh_queued
    assert ("invalidate",) in enable_calls
    assert (
        "compare",
        {"prefetch": True, "force": True, "immediate_paint": False},
    ) in enable_calls
    assert any(c[0] == "meters" and c[1].get("force") for c in enable_calls)

    calls: list[tuple] = []
    dlg._invalidate_machine_cabinet_cache = lambda: calls.append(("invalidate",))  # type: ignore[method-assign]
    dlg._begin_cabinet_compare = lambda **kw: calls.append(("compare", kw))  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: calls.append(("meters", kw)) or True  # type: ignore[method-assign]

    # First poll for a root records the baseline only.
    dlg._on_state_mtime_polled(100.0, "rootA")
    assert dlg._auto_fetch_baseline_mtime == 100.0
    assert calls == []

    # Unchanged mtime: no fetch.
    dlg._on_state_mtime_polled(100.0, "rootA")
    assert calls == []

    # Newer mtime: EGM wrote fresh state -> auto refetch Machine + SAS. The
    # refresh that just ran on enable holds off the next one, so this change is
    # queued rather than started on top of it.
    dlg._on_state_mtime_polled(250.0, "rootA")
    assert dlg._auto_fetch_refresh_queued
    assert calls == []
    assert dlg._auto_fetch_queue_timer.interval() > 0
    dlg._auto_fetch_last_force_mono = 0.0  # pretend the spacing window elapsed
    dlg._run_queued_auto_fetch_refresh()
    assert ("invalidate",) in calls
    assert ("compare", {"prefetch": True, "force": True, "immediate_paint": False}) in calls
    assert any(c[0] == "meters" and c[1].get("force") for c in calls)
    assert dlg._auto_fetch_baseline_mtime == 250.0

    # A burst of state writes coalesces into the one pending refresh.
    calls.clear()
    dlg._auto_fetch_last_force_mono = 0.0
    dlg._on_state_mtime_polled(300.0, "rootA")
    dlg._on_state_mtime_polled(400.0, "rootA")
    dlg._on_state_mtime_polled(500.0, "rootA")
    assert calls == []
    dlg._run_queued_auto_fetch_refresh()
    assert len([c for c in calls if c[0] == "compare"]) == 1
    assert len([c for c in calls if c[0] == "meters"]) == 1
    calls.clear()
    dlg._auto_fetch_last_force_mono = 0.0

    # Root change resets the baseline instead of fetching.
    calls.clear()
    dlg._on_state_mtime_polled(999.0, "rootB")
    assert calls == []
    assert dlg._auto_fetch_baseline_mtime == 999.0

    # Stale in-flight poll: a result for the *previous* root must not trigger a
    # fetch after the user switched the scan root (its mtime belongs to another
    # cabinet). It is dropped without touching the baseline.
    calls.clear()
    blocked = dlg._scan_root_edit.blockSignals(True)
    dlg._scan_root_edit.setText("rootC")
    dlg._scan_root_edit.blockSignals(blocked)
    dlg._scan_root = "rootC"
    dlg._on_state_mtime_polled(5_000.0, "rootB")
    assert calls == []
    assert dlg._auto_fetch_baseline_root == "rootB"  # untouched
    assert dlg._auto_fetch_baseline_mtime == 999.0
    # The next poll of the current root rebaselines cleanly.
    dlg._on_state_mtime_polled(50.0, "rootC")
    assert calls == []
    assert dlg._auto_fetch_baseline_root == "rootC"
    assert dlg._auto_fetch_baseline_mtime == 50.0
    dlg._scan_root = ""
    blocked = dlg._scan_root_edit.blockSignals(True)
    dlg._scan_root_edit.setText("")
    dlg._scan_root_edit.blockSignals(blocked)

    dlg._auto_fetch_toggle.setChecked(False)
    assert not dlg._auto_fetch_timer.isActive()

    dlg._mark_meters_fetched()
    assert re.fullmatch(
        r"Meters fetched \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
        dlg._fetched_status_label.text(),
    )

    dlg.deleteLater()
    app.processEvents()


def test_status_strips_stay_thin() -> None:
    """Busy line + prefetch line keep a fixed thin height across states."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog
    from gui.thin_progress import THIN_BUSY_HEIGHT_PX

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root="",
    )
    dlg._prefetch_started = True
    dlg.show()
    app.processEvents()

    assert dlg._busy_progress.height() == THIN_BUSY_HEIGHT_PX
    dlg._set_busy_progress_active(True)
    app.processEvents()
    assert dlg._busy_progress.height() == THIN_BUSY_HEIGHT_PX

    dlg._set_prefetch_status_text("x" * 400)
    app.processEvents()
    assert dlg._prefetch_status_label.height() == 18
    assert dlg._prefetch_status_label.wordWrap() is False

    dlg.deleteLater()
    app.processEvents()


def test_begin_cabinet_compare_accepts_immediate_paint() -> None:
    """Auto fetch passes immediate_paint=False; must not TypeError."""
    import inspect

    from gui.sas_verify_dialog import SasVerifyDialog

    sig = inspect.signature(SasVerifyDialog._begin_cabinet_compare)
    assert "immediate_paint" in sig.parameters
    assert sig.parameters["immediate_paint"].default is True


def test_second_refresh_queues_forced_recapture() -> None:
    """A second Refresh while COM is busy must queue, not no-op."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root="",
    )
    dlg._prefetch_started = True
    dlg._meter_fetch_running = lambda: True  # type: ignore[method-assign]
    dlg._compare_running = lambda: False  # type: ignore[method-assign]
    dlg._paint_immediate_busy_feedback = lambda: None  # type: ignore[method-assign]

    dlg._on_get_meters_clicked()
    assert dlg._pending_forced_fetch == {"prefetch": False, "full_timing": True}
    assert "Queued" in (dlg._prefetch_status_full or "")

    dlg.deleteLater()
    app.processEvents()


def test_latest_device_state_mtime_includes_aft_xml(tmp_path, monkeypatch) -> None:
    """AFT transaction XML must move the auto-fetch mtime, not only DeviceManagerData."""
    import os

    import network.goldclub_paths as gp
    from network.accounting_state_loader import latest_device_state_mtime

    state = tmp_path / "state"
    (state / "SASControler1").mkdir(parents=True)
    (state / "gm2au").mkdir(parents=True)
    dm = state / "gm2au" / "DeviceManagerData.xml_1"
    aft = state / "SASControler1" / "GCC_ST_local_01_aftMostRecentTransaction_v1.xml"
    dm.write_text("<x/>", encoding="utf-8")
    aft.write_text("<aft/>", encoding="utf-8")
    os.utime(dm, (1_000_000, 1_000_000))
    os.utime(aft, (3_000_000, 3_000_000))

    layout = SimpleNamespace(state_gcmessenger=state)
    monkeypatch.setattr(gp, "resolve_goldclub_layout", lambda root: layout)

    assert latest_device_state_mtime(r"C:\whatever\Goldclub\var") == 3_000_000


def test_force_auto_fetch_queues_young_compare_instead_of_restart() -> None:
    """Auto fetch must not thrash a healthy in-flight Machine load / Refresh."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"C:\Goldclub\var\log",
    )
    dlg._prefetch_started = True
    dlg._resolve_active_scan_root = lambda: r"C:\Goldclub\var\log"  # type: ignore[method-assign]
    dlg._cabinet_cache_valid = lambda: False  # type: ignore[method-assign]
    dlg._invalidate_machine_cabinet_cache = lambda: None  # type: ignore[method-assign]
    dlg._paint_immediate_busy_feedback = lambda: None  # type: ignore[method-assign]
    dlg._update_prefetch_status = lambda *a, **k: None  # type: ignore[method-assign]
    dlg._machine_loaded_for_current_root = lambda: True  # type: ignore[method-assign]
    dlg._compare_running = lambda: True  # type: ignore[method-assign]
    dlg._compare_started_mono = __import__("time").monotonic()
    stops: list[int] = []
    dlg._stop_compare_thread = lambda wait_ms=300: stops.append(wait_ms) or True  # type: ignore[method-assign]

    SasVerifyDialog._begin_cabinet_compare(
        dlg, prefetch=True, force=True, immediate_paint=False
    )
    assert dlg._cabinet_compare_force_pending
    assert stops == []

    dlg.deleteLater()
    app.processEvents()


def test_force_auto_fetch_restarts_stuck_compare() -> None:
    """A compare stuck >=20s is still restarted so Loading Machine cannot hang forever."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui import sas_verify_dialog as mod
    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"C:\Goldclub\var\log",
    )
    dlg._prefetch_started = True
    dlg._resolve_active_scan_root = lambda: r"C:\Goldclub\var\log"  # type: ignore[method-assign]
    dlg._cabinet_cache_valid = lambda: False  # type: ignore[method-assign]
    dlg._invalidate_machine_cabinet_cache = lambda: None  # type: ignore[method-assign]
    dlg._paint_immediate_busy_feedback = lambda: None  # type: ignore[method-assign]
    dlg._update_prefetch_status = lambda *a, **k: None  # type: ignore[method-assign]
    dlg._machine_loaded_for_current_root = lambda: True  # type: ignore[method-assign]

    running = {"v": True}
    dlg._compare_running = lambda: running["v"]  # type: ignore[method-assign]
    dlg._compare_started_mono = __import__("time").monotonic() - 25.0
    stops: list[int] = []

    def _stop(wait_ms: int = 300) -> bool:
        stops.append(wait_ms)
        running["v"] = False
        dlg._compare_thread = None
        dlg._compare_worker = None
        return False

    dlg._stop_compare_thread = _stop  # type: ignore[method-assign]
    started: list[bool] = []

    class _NoopWorker:
        def __init__(self, *a, **k) -> None:
            pass

        def moveToThread(self, *a, **k) -> None:
            return None

        def run(self) -> None:
            return None

        def deleteLater(self) -> None:
            return None

        @property
        def finished(self):
            return SimpleNamespace(connect=lambda *a, **k: None)

        @property
        def error(self):
            return SimpleNamespace(connect=lambda *a, **k: None)

    class _NoopThread:
        def __init__(self, *a, **k) -> None:
            pass

        def isRunning(self) -> bool:
            return False

        def start(self) -> None:
            started.append(True)

        def quit(self) -> None:
            return None

        def deleteLater(self) -> None:
            return None

        @property
        def started(self):
            return SimpleNamespace(connect=lambda *a, **k: None)

        @property
        def finished(self):
            return SimpleNamespace(connect=lambda *a, **k: None)

    orig_qthread = mod.QThread
    orig_worker = mod.CompareWorker
    mod.QThread = _NoopThread  # type: ignore[misc,assignment]
    mod.CompareWorker = _NoopWorker  # type: ignore[misc,assignment]
    try:
        SasVerifyDialog._begin_cabinet_compare(
            dlg, prefetch=True, force=True, immediate_paint=False
        )
    finally:
        mod.QThread = orig_qthread  # type: ignore[misc]
        mod.CompareWorker = orig_worker  # type: ignore[misc]

    assert stops, "expected stuck compare to be stopped/orphaned"
    assert started, "expected a replacement compare worker to start"
    assert not dlg._cabinet_compare_force_pending

    dlg.deleteLater()
    app.processEvents()


def test_local_auto_fetch_skips_com_and_ends_round() -> None:
    """On the EGM itself Auto fetch must reload files only — never open COM."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"C:\Goldclub\var\log",
    )
    dlg._prefetch_started = True
    dlg._local_files_only_mode = lambda: True  # type: ignore[method-assign]
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    calls: list[tuple] = []
    compare_live = {"v": False}

    def _fake_compare(**kw):
        calls.append(("compare", kw))
        compare_live["v"] = True

    dlg._invalidate_machine_cabinet_cache = lambda: calls.append(("invalidate",))  # type: ignore[method-assign]
    dlg._begin_cabinet_compare = _fake_compare  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: calls.append(("meters", kw)) or True  # type: ignore[method-assign]
    dlg._compare_running = lambda: compare_live["v"]  # type: ignore[method-assign]

    dlg._auto_fetch_refresh_queued = True
    dlg._auto_fetch_last_force_mono = 0.0
    dlg._run_queued_auto_fetch_refresh()

    assert ("invalidate",) in calls
    assert (
        "compare",
        {"prefetch": True, "force": True, "immediate_paint": False},
    ) in calls
    assert not any(c[0] == "meters" for c in calls)
    assert dlg._auto_fetch_round_active

    dlg.deleteLater()
    app.processEvents()


def test_begin_meter_fetch_aborts_on_local_files_only() -> None:
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"C:\Goldclub\var\log",
    )
    dlg._prefetch_started = True
    dlg._local_files_only_mode = lambda: True  # type: ignore[method-assign]
    assert dlg._begin_meter_fetch(prefetch=True, force=True) is False
    dlg.deleteLater()
    app.processEvents()


def test_manual_refresh_blocks_auto_fetch_until_idle() -> None:
    """Refresh Meters while Auto fetch is on must not start a second reload yet."""
    from gui.sas_verify_dialog import (
        AUTO_FETCH_BUSY_RETRY_S,
        auto_fetch_refresh_delay_s,
    )

    delay = auto_fetch_refresh_delay_s(
        now_mono=1000.0, last_refresh_mono=0.0, busy=True, min_refresh_s=0.4
    )
    assert delay == AUTO_FETCH_BUSY_RETRY_S


def test_manual_force_queues_when_compare_fresh() -> None:
    """Manual force with a young in-flight compare still queues (no restart yet)."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"C:\Goldclub\var\log",
    )
    dlg._prefetch_started = True
    dlg._resolve_active_scan_root = lambda: r"C:\Goldclub\var\log"  # type: ignore[method-assign]
    dlg._cabinet_cache_valid = lambda: False  # type: ignore[method-assign]
    dlg._invalidate_machine_cabinet_cache = lambda: None  # type: ignore[method-assign]
    dlg._paint_immediate_busy_feedback = lambda: None  # type: ignore[method-assign]
    dlg._compare_running = lambda: True  # type: ignore[method-assign]
    dlg._compare_started_mono = __import__("time").monotonic()
    stops: list[int] = []
    dlg._stop_compare_thread = lambda wait_ms=300: stops.append(wait_ms) or True  # type: ignore[method-assign]

    SasVerifyDialog._begin_cabinet_compare(
        dlg, prefetch=False, force=True, immediate_paint=True
    )
    assert dlg._cabinet_compare_force_pending
    assert stops == []

    dlg.deleteLater()
    app.processEvents()


def test_auto_fetch_refresh_survives_compare_kwargs() -> None:
    """Queued Auto fetch must call compare with immediate_paint and not raise."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root="",
    )
    dlg._prefetch_started = True
    calls: list[tuple] = []
    dlg._invalidate_machine_cabinet_cache = lambda: calls.append(("invalidate",))  # type: ignore[method-assign]
    dlg._begin_cabinet_compare = lambda **kw: calls.append(("compare", kw))  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: calls.append(("meters", kw)) or True  # type: ignore[method-assign]

    dlg._auto_fetch_refresh_queued = True
    dlg._auto_fetch_last_force_mono = 0.0
    dlg._run_queued_auto_fetch_refresh()

    assert (
        "compare",
        {"prefetch": True, "force": True, "immediate_paint": False},
    ) in calls
    assert any(c[0] == "meters" and c[1].get("force") for c in calls)
    assert dlg._auto_fetch_round_active

    dlg.deleteLater()
    app.processEvents()

