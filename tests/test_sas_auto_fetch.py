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


def test_local_meter_pair_ready_waits_for_both_folders() -> None:
    """gm2au can lag SASControler1 by seconds — do not read the early side alone."""
    from network.accounting_state_loader import local_meter_pair_ready

    baseline = {"gm2au": 100.0, "SASControler1": 100.0}
    # Only SAS advanced: hold.
    ready, saw = local_meter_pair_ready(
        baseline=baseline,
        current={"gm2au": 100.0, "SASControler1": 200.0},
        waited_s=0.2,
        max_wait_s=1.5,
    )
    assert saw is True
    assert ready is False
    # Both advanced: go.
    ready, saw = local_meter_pair_ready(
        baseline=baseline,
        current={"gm2au": 201.0, "SASControler1": 200.0},
        waited_s=0.2,
        max_wait_s=1.5,
    )
    assert ready is True and saw is True
    # Budget expired with one-sided write (AFT-only): go anyway.
    ready, saw = local_meter_pair_ready(
        baseline=baseline,
        current={"gm2au": 100.0, "SASControler1": 200.0},
        waited_s=1.5,
        max_wait_s=1.5,
    )
    assert ready is True and saw is True


def test_local_auto_fetch_skips_compare_worker() -> None:
    """On-cabinet Auto fetch must not pay for CompareWorker + LocalDiff."""
    import inspect

    from gui.sas_verify_dialog import (
        AUTO_FETCH_MIN_REFRESH_LOCAL_S,
        AUTO_FETCH_ROUND_IDLE_S,
        SasVerifyDialog,
    )

    src = inspect.getsource(SasVerifyDialog._run_queued_auto_fetch_refresh)
    assert "_run_queued_local_pair_refresh" in src
    assert AUTO_FETCH_MIN_REFRESH_LOCAL_S <= 0.5
    assert AUTO_FETCH_ROUND_IDLE_S <= 1.0


def test_auto_fetch_remote_cadence_is_fast() -> None:
    """A played game must show within ~1-2 s on a remote UNC root.

    Measured on \\\\10.0.0.90: the SMB stat is ~30-40 ms and the full Machine
    read ~40 ms, so anything above a 1 s poll / 1.5 s refresh spacing is pure
    self-imposed lag (the old 4 s + 3 s cadence stacked to ~10 s per game).
    """
    from gui.sas_verify_dialog import AUTO_FETCH_POLL_INTERVAL_MS

    assert AUTO_FETCH_POLL_INTERVAL_MS <= 1000
    assert AUTO_FETCH_MIN_REFRESH_S <= 1.5


def test_auto_fetch_refresh_delay_first_refresh_runs_now() -> None:
    assert (
        auto_fetch_refresh_delay_s(now_mono=500.0, last_refresh_mono=0.0, busy=False) == 0.0
    )


def test_auto_fetch_refresh_delay_spaces_out_bursts() -> None:
    # Partway into the spacing window: wait out the rest of it.
    half = AUTO_FETCH_MIN_REFRESH_S / 2.0
    delay = auto_fetch_refresh_delay_s(
        now_mono=500.0 + half, last_refresh_mono=500.0, busy=False
    )
    assert abs(delay - (AUTO_FETCH_MIN_REFRESH_S - half)) < 1e-9
    # Window already elapsed: run immediately.
    assert (
        auto_fetch_refresh_delay_s(
            now_mono=500.0 + AUTO_FETCH_MIN_REFRESH_S, last_refresh_mono=500.0, busy=False
        )
        == 0.0
    )
    # Local EGM: short coalesce; dual-source pair wait absorbs write storms.
    from gui.sas_verify_dialog import AUTO_FETCH_MIN_REFRESH_LOCAL_S

    assert 0.2 <= AUTO_FETCH_MIN_REFRESH_LOCAL_S <= 0.5
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
    # The longer of the two brakes wins (elapsed 0 → full spacing remains).
    assert auto_fetch_refresh_delay_s(
        now_mono=500.0, last_refresh_mono=500.0, busy=True
    ) == max(AUTO_FETCH_MIN_REFRESH_S, AUTO_FETCH_BUSY_RETRY_S)


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



def test_unchecking_auto_fetch_suppresses_busy_helpers() -> None:
    """Turning Auto fetch off still marks busy helpers suppressed (bar is gone)."""
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
    assert dlg._busy_progress is None
    dlg._set_busy_progress_active(True)

    dlg._meter_fetch_running = lambda: True  # type: ignore[method-assign]
    dlg._auto_fetch_toggle.setChecked(False)

    assert dlg._busy_progress is None
    assert dlg._busy_idle_timer is None
    assert dlg._busy_progress_suppressed is True
    dlg._set_busy_progress_active(True)
    assert dlg._busy_progress is None

    dlg.deleteLater()
    app.processEvents()


def test_dialog_auto_fetch_toggle_behavior() -> None:
    """Headless Qt: on at launch, timer lifecycle, refetch on newer mtime."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import (
        _KEY_AUTO_FETCH,
        SasVerifyDialog,
        _sas_verify_settings,
    )

    # Pref lives at sasVerify/auto_fetch (not SasVerifyDialog/auto_fetch).
    s = _sas_verify_settings()
    s.remove(f"sasVerify/{_KEY_AUTO_FETCH}")
    s.sync()

    app = QApplication.instance() or QApplication([])
    vm = SimpleNamespace(current_product_name="GUI-Test")
    dlg = SasVerifyDialog(vm, QThreadPool.globalInstance(), scan_root="")
    dlg._prefetch_started = True  # avoid COM/cabinet prefetch side effects in CI
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]

    # Checked at every launch; watcher arms on show (or explicit toggle).
    assert dlg._auto_fetch_toggle.isChecked()
    assert not dlg._auto_fetch_timer.isActive()

    enable_calls: list[tuple] = []
    dlg._invalidate_machine_cabinet_cache = lambda: enable_calls.append(("invalidate",))  # type: ignore[method-assign]
    dlg._begin_cabinet_compare = lambda **kw: enable_calls.append(("compare", kw))  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: enable_calls.append(("meters", kw)) or True  # type: ignore[method-assign]

    # Same path as showEvent: arm while checked. Refresh is queued, not inline.
    dlg._arm_default_auto_fetch()
    assert dlg._auto_fetch_timer.isActive()
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

    dlg._auto_fetch_toggle.setChecked(False)
    assert not dlg._auto_fetch_timer.isActive()
    dlg._auto_fetch_toggle.setChecked(True)
    assert dlg._auto_fetch_timer.isActive()
    assert dlg._auto_fetch_refresh_queued

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
    # First enable-refresh left the round open (mocked workers never finish).
    # A real round ends before the next queued refresh runs.
    dlg._auto_fetch_round_active = False
    dlg._auto_fetch_last_force_mono = 0.0  # pretend the spacing window elapsed
    dlg._run_queued_auto_fetch_refresh()
    assert ("invalidate",) in calls
    assert ("compare", {"prefetch": True, "force": True, "immediate_paint": False}) in calls
    assert any(c[0] == "meters" and c[1].get("force") for c in calls)
    assert dlg._auto_fetch_baseline_mtime == 250.0

    # A burst of state writes coalesces into the one pending refresh.
    calls.clear()
    dlg._auto_fetch_round_active = False
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
    # Leave pref on so later tests that rely on the default are not poisoned.
    s2 = _sas_verify_settings()
    s2.remove(f"sasVerify/{_KEY_AUTO_FETCH}")
    s2.sync()

    dlg._mark_meters_fetched()
    assert re.fullmatch(
        r"Meters fetched \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
        dlg._fetched_status_label.text(),
    )

    dlg.deleteLater()
    app.processEvents()


def test_status_strips_stay_thin() -> None:
    """Prefetch status line stays fixed-height; busy bar is not in the UI."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import (
        _KEY_AUTO_FETCH,
        SasVerifyDialog,
        _sas_verify_settings,
    )

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root="",
    )
    dlg._prefetch_started = True
    # showEvent arms Auto fetch via QTimer — keep it off so no COM/cabinet work starts.
    blocked = dlg._auto_fetch_toggle.blockSignals(True)
    dlg._auto_fetch_toggle.setChecked(False)
    dlg._auto_fetch_toggle.blockSignals(blocked)
    dlg.show()
    app.processEvents()

    assert dlg._busy_progress is None

    dlg._set_prefetch_status_text("x" * 400)
    app.processEvents()
    assert dlg._prefetch_status_label.height() == 18
    assert dlg._prefetch_status_label.wordWrap() is False

    dlg._accept_worker_signals = False
    for name in (
        "_auto_fetch_timer",
        "_auto_fetch_queue_timer",
        "_settle_repaint_timer",
        "_meter_flash_timer",
        "_recovery_timer",
    ):
        timer = getattr(dlg, name, None)
        if timer is not None:
            timer.stop()
    s = _sas_verify_settings()
    s.remove(f"sasVerify/{_KEY_AUTO_FETCH}")
    s.sync()
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


def test_latest_device_state_mtime_ignores_aft_history_archive(
    tmp_path, monkeypatch
) -> None:
    """History dumps must not be statted — they balloon UNC Auto-fetch polls."""
    import os

    import network.goldclub_paths as gp
    from network.accounting_state_loader import (
        device_state_watch_files,
        latest_device_state_mtime,
    )

    state = tmp_path / "state"
    sas = state / "SASControler1"
    hist = sas / "History"
    (state / "gm2au").mkdir(parents=True)
    sas.mkdir(parents=True)
    hist.mkdir(parents=True)
    dm = state / "gm2au" / "DeviceManagerData.xml_1"
    live = sas / "GCC_ST_local_01_aftMostRecentTransaction_v1.xml"
    archive = sas / "GCC_ST_local_01_aftTransactionHistory_i99_v1.xml"
    buried = hist / "GCC_ST_local_01_aftTransactionHistory_i1_v1.xml"
    dm.write_text("<x/>", encoding="utf-8")
    live.write_text("<aft/>", encoding="utf-8")
    archive.write_text("<old/>", encoding="utf-8")
    buried.write_text("<old/>", encoding="utf-8")
    os.utime(dm, (1_000_000, 1_000_000))
    os.utime(live, (2_000_000, 2_000_000))
    os.utime(archive, (9_000_000, 9_000_000))
    os.utime(buried, (9_500_000, 9_500_000))

    watched = {p.name for p in device_state_watch_files(state)}
    assert "GCC_ST_local_01_aftMostRecentTransaction_v1.xml" in watched
    assert "GCC_ST_local_01_aftTransactionHistory_i99_v1.xml" not in watched
    assert "GCC_ST_local_01_aftTransactionHistory_i1_v1.xml" not in watched

    layout = SimpleNamespace(state_gcmessenger=state)
    monkeypatch.setattr(gp, "resolve_goldclub_layout", lambda root: layout)
    assert latest_device_state_mtime(r"C:\whatever\Goldclub\var") == 2_000_000


def test_local_egm_status_never_promises_sas_mux() -> None:
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
    dlg._compare_running = lambda: False  # type: ignore[method-assign]
    dlg._meter_fetch_running = lambda: False  # type: ignore[method-assign]
    dlg._local_diff_running = True
    dlg._update_prefetch_status()
    text = dlg._prefetch_status_label.text()
    assert "will appear automatically" not in text
    assert "Cabinet loading" not in text
    assert "local files" in text.lower()

    dlg._local_diff_running = False
    dlg._machine_state_loaded = True
    dlg._loaded_cabinet_scan_root = r"C:\Goldclub\var\log"
    dlg._scan_root = r"C:\Goldclub\var\log"
    dlg._update_prefetch_status()
    idle = dlg._prefetch_status_label.text()
    assert "will appear automatically" not in idle
    assert "Cabinet loading" not in idle

    # Idle + empty must not claim "Loading…" (nothing is in flight).
    dlg._machine_state_loaded = False
    dlg._loaded_cabinet_scan_root = ""
    dlg._update_prefetch_status()
    empty = dlg._prefetch_status_label.text()
    assert "Loading Machine meters" not in empty
    assert "not found" in empty.lower()

    dlg.deleteLater()
    app.processEvents()


def test_local_diff_end_round_does_not_double_render() -> None:
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
    dlg._resolved_game_client_kind = lambda: "slot"  # type: ignore[method-assign]
    dlg._reload_game_theme_catalog = lambda: None  # type: ignore[method-assign]
    dlg._schedule_game_theme_catalog_reload = lambda: None  # type: ignore[method-assign]
    dlg._seed_verify_rows_from_cabinet_if_needed = lambda: None  # type: ignore[method-assign]
    dlg._machine_state = {"coinin": "10"}
    dlg._machine_state_loaded = True
    dlg._loaded_cabinet_scan_root = r"C:\Goldclub\var\log"
    dlg._scan_root = r"C:\Goldclub\var\log"
    dlg._last_parsed_rows = [SimpleNamespace()]  # truthy stand-in
    renders: list[bool] = []
    dlg._render = lambda **kw: renders.append(True)  # type: ignore[method-assign]
    dlg._auto_fetch_round_active = True

    dlg._on_local_diff_done(
        "",
        {"sas": {"coinin": "10"}, "machine": {"coinin": "10"}},
    )
    assert renders == [True]
    assert dlg._auto_fetch_round_active is False

    dlg.deleteLater()
    app.processEvents()


def test_auto_fetch_delay_treats_open_round_as_busy() -> None:
    """Do not start a second Auto-fetch round while the first is still settling."""
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
    dlg._auto_fetch_round_active = True
    dlg._meter_fetch_running = lambda: False  # type: ignore[method-assign]
    dlg._compare_running = lambda: False  # type: ignore[method-assign]
    dlg._local_diff_running = False
    dlg._manual_meters_refresh = False
    assert dlg._auto_fetch_refresh_delay_now() >= AUTO_FETCH_BUSY_RETRY_S

    dlg.deleteLater()
    app.processEvents()


def test_local_diff_in_flight_does_not_start_force_pending_compare() -> None:
    """EGM play storm: finishing Machine must not immediately start another load."""
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
    dlg._reload_game_theme_catalog = lambda: None  # type: ignore[method-assign]
    dlg._schedule_game_theme_catalog_reload = lambda: None  # type: ignore[method-assign]
    dlg._pool = SimpleNamespace(start=lambda *_a, **_k: None)  # type: ignore[assignment]
    starts: list[dict] = []
    dlg._begin_cabinet_compare = lambda **kw: starts.append(kw)  # type: ignore[method-assign]
    dlg._run_cabinet_ui_refresh = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("UI refresh must wait for LocalDiff on local EGM")
    )
    dlg._cabinet_compare_force_pending = True
    dlg._auto_fetch_round_active = True

    dlg._apply_cabinet_state({"coinin": "1000"})
    assert dlg._local_diff_running is True
    assert dlg._cabinet_compare_force_pending is True
    assert starts == []

    dlg.deleteLater()
    app.processEvents()


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
    """On the EGM itself Auto fetch is one LocalDiff — never CompareWorker or COM."""
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
    blocked = dlg._auto_fetch_toggle.blockSignals(True)
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._auto_fetch_toggle.blockSignals(blocked)
    calls: list[str] = []

    dlg._begin_cabinet_compare = lambda **kw: calls.append("compare")  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: calls.append("meters") or True  # type: ignore[method-assign]
    dlg._begin_local_pair_diff = lambda: calls.append("local_pair")  # type: ignore[method-assign]
    # Both folders already advanced past the empty baseline → ready immediately.
    import network.accounting_state_loader as asl

    dlg._auto_fetch_source_mtimes = {}
    asl_device = asl.device_source_mtimes
    asl.device_source_mtimes = lambda _sr: {  # type: ignore[assignment]
        "gm2au": 10.0,
        "SASControler1": 10.0,
    }

    try:
        dlg._auto_fetch_refresh_queued = True
        dlg._auto_fetch_last_force_mono = 0.0
        dlg._run_queued_auto_fetch_refresh()
    finally:
        asl.device_source_mtimes = asl_device  # type: ignore[assignment]

    assert "local_pair" in calls
    assert "compare" not in calls
    assert "meters" not in calls
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
    blocked = dlg._auto_fetch_toggle.blockSignals(True)
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._auto_fetch_toggle.blockSignals(blocked)
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


def test_invalidate_keeps_machine_snapshot_for_autofetch() -> None:
    """Auto fetch must not flash NO MACHINE by clearing the loaded snapshot."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    root = r"C:\Goldclub\var\log"
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=root,
    )
    dlg._prefetch_started = True
    dlg._auto_fetch_toggle.setChecked(False)
    dlg._machine_state = {"coinin": "100"}
    dlg._machine_state_loaded = True
    dlg._loaded_cabinet_scan_root = root
    dlg._machine_state_loaded_at = 1_000.0
    dlg._scan_root = root

    dlg._invalidate_machine_cabinet_cache()
    assert dlg._machine_state == {"coinin": "100"}
    assert dlg._machine_state_loaded is True
    assert dlg._loaded_cabinet_scan_root == root
    assert dlg._machine_state_loaded_at == 0.0
    assert dlg._machine_loaded_for_current_root()
    assert not dlg._cabinet_cache_valid()

    dlg._invalidate_machine_cabinet_cache(wipe=True)
    assert dlg._machine_state == {}
    assert dlg._machine_state_loaded is False

    dlg.deleteLater()
    app.processEvents()


def test_empty_machine_reload_keeps_previous_during_autofetch() -> None:
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    root = r"C:\Goldclub\var\log"
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=root,
    )
    dlg._prefetch_started = True
    # Keep Auto fetch "on" without starting real cabinet/COM work.
    blocked = dlg._auto_fetch_toggle.blockSignals(True)
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._auto_fetch_toggle.blockSignals(blocked)
    dlg._auto_fetch_timer.stop()
    dlg._scan_root = root
    dlg._machine_state = {"coinin": "250"}
    dlg._machine_state_loaded = True
    dlg._loaded_cabinet_scan_root = root
    dlg._machine_state_loaded_at = 50.0
    dlg._active_compare_job_id = 1
    dlg._schedule_empty_machine_retry = lambda: None  # type: ignore[method-assign]
    dlg._run_cabinet_ui_refresh = lambda: None  # type: ignore[method-assign]
    dlg._reload_game_theme_catalog = lambda: None  # type: ignore[method-assign]
    dlg._schedule_game_theme_catalog_reload = lambda: None  # type: ignore[method-assign]

    dlg._apply_cabinet_state({}, job_id=1)
    assert dlg._machine_state == {"coinin": "250"}
    assert dlg._machine_state_loaded is True
    assert dlg._loaded_cabinet_scan_root == root

    dlg._auto_fetch_toggle.setChecked(False)
    dlg.deleteLater()
    app.processEvents()


def test_com_failure_ends_autofetch_round_even_during_recovery(monkeypatch) -> None:
    """COM access-denied must not leave SYNCING / round_active stuck forever."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    import gui.sas_verify_dialog as mod
    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(mod, "igt_sas_tester_is_running", lambda **kw: False)
    monkeypatch.setattr(mod, "com_error_is_port_busy", lambda msg: True)
    monkeypatch.setattr(mod, "com_error_is_link_dead", lambda msg: False)
    # The manual (non-prefetch) path ends in a modal warning — swallow it or
    # pytest blocks forever inside QMessageBox.exec().
    boxes: list[str] = []
    monkeypatch.setattr(
        mod.QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: boxes.append("warned")),
    )
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root="",
    )
    dlg._prefetch_started = True
    dlg._auto_fetch_toggle.setChecked(False)
    dlg._auto_fetch_round_active = True
    dlg._com_recovery_pending = True
    dlg._game_recovery_pending = False
    dlg._meter_fetch_running = lambda: False  # type: ignore[method-assign]
    dlg._flush_pending_cabinet_ui_refresh = lambda: None  # type: ignore[method-assign]
    dlg._refresh_com_port_list = lambda **kw: None  # type: ignore[method-assign]
    dlg._update_onehand_warning_label = lambda *_a, **_k: None  # type: ignore[method-assign]
    dlg._update_prefetch_status = lambda *_a, **_k: None  # type: ignore[method-assign]
    dlg._resolved_com_port_for_fetch = lambda: "COM4"  # type: ignore[method-assign]

    dlg._on_meter_fetch_error("Could not open COM4: Access is denied.")
    assert dlg._auto_fetch_round_active is False

    dlg.deleteLater()
    app.processEvents()


def test_arm_default_auto_fetch_skips_refresh_while_prefetch_compare_runs() -> None:
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
    blocked = dlg._auto_fetch_toggle.blockSignals(True)
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._auto_fetch_toggle.blockSignals(blocked)
    dlg._auto_fetch_timer.stop()
    dlg._auto_fetch_refresh_queued = False
    dlg._compare_running = lambda: True  # type: ignore[method-assign]
    dlg._meter_fetch_running = lambda: False  # type: ignore[method-assign]
    dlg._local_diff_running = False
    dlg._cabinet_compare_force_pending = False
    dlg._begin_cabinet_compare = lambda **kw: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("must not force-refresh while prefetch compare runs")
    )
    dlg._begin_meter_fetch = lambda **kw: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("must not start COM while prefetch compare runs")
    )

    dlg._arm_default_auto_fetch()
    assert dlg._auto_fetch_timer.isActive()
    assert not dlg._auto_fetch_refresh_queued

    dlg._auto_fetch_toggle.setChecked(False)
    dlg.deleteLater()
    app.processEvents()


def _autofetch_on_dialog(scan_root: str):
    """Dialog with Auto fetch checked but nothing armed / no real work started."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import (
        _KEY_AUTO_FETCH,
        SasVerifyDialog,
        _sas_verify_settings,
    )

    # Keep suite default (True) — do not inherit a prior test's false pref.
    s = _sas_verify_settings()
    s.remove(f"sasVerify/{_KEY_AUTO_FETCH}")
    s.sync()

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=scan_root,
    )
    dlg._prefetch_started = True
    blocked = dlg._auto_fetch_toggle.blockSignals(True)
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._auto_fetch_toggle.blockSignals(blocked)
    for name in (
        "_auto_fetch_timer",
        "_auto_fetch_queue_timer",
        "_settle_repaint_timer",
        "_meter_flash_timer",
        "_recovery_timer",
    ):
        timer = getattr(dlg, name, None)
        if timer is not None:
            timer.stop()
    dlg._scan_root = scan_root
    return app, dlg


def _close_autofetch_dialog(app, dlg) -> None:
    """Tear down without persisting auto_fetch=false or racing dying timers."""
    from gui.sas_verify_dialog import _KEY_AUTO_FETCH, _sas_verify_settings

    dlg._accept_worker_signals = False
    for name in (
        "_auto_fetch_timer",
        "_auto_fetch_queue_timer",
        "_settle_repaint_timer",
        "_meter_flash_timer",
        "_recovery_timer",
    ):
        timer = getattr(dlg, name, None)
        if timer is not None:
            timer.stop()
    try:
        dlg._stop_meter_fetch_thread(wait_ms=200)
    except Exception:
        pass
    try:
        dlg._stop_compare_thread(wait_ms=200)
    except Exception:
        pass
    toggle = getattr(dlg, "_auto_fetch_toggle", None)
    if toggle is not None:
        blocked = toggle.blockSignals(True)
        toggle.setChecked(False)
        toggle.blockSignals(blocked)
    s = _sas_verify_settings()
    s.remove(f"sasVerify/{_KEY_AUTO_FETCH}")
    s.sync()
    dlg.deleteLater()
    app.processEvents()


def test_empty_machine_retry_is_compare_only() -> None:
    """An empty state file is no reason to re-open COM every retry cycle."""
    root = r"C:\Goldclub\var\log"
    app, dlg = _autofetch_on_dialog(root)
    calls: list[str] = []
    dlg._begin_cabinet_compare = lambda **kw: calls.append("compare")  # type: ignore[method-assign]
    dlg._begin_meter_fetch = lambda **kw: calls.append("fetch") or True  # type: ignore[method-assign]
    dlg._queue_auto_fetch_refresh = lambda: calls.append("full_round")  # type: ignore[method-assign]
    dlg._compare_running = lambda: False  # type: ignore[method-assign]
    dlg._local_diff_running = False
    # Kept snapshot, soft-expired (the keep-on-empty state).
    dlg._machine_state = {"coinin": "250"}
    dlg._machine_state_loaded = True
    dlg._loaded_cabinet_scan_root = root
    dlg._machine_state_loaded_at = 0.0

    dlg._retry_empty_machine_reload()
    assert calls == ["compare"]
    assert dlg._machine_empty_retry_count == 1

    _close_autofetch_dialog(app, dlg)


def test_empty_machine_retry_gives_up_after_cap() -> None:
    from gui.sas_verify_dialog import AUTO_FETCH_EMPTY_MACHINE_RETRY_MAX

    root = r"C:\Goldclub\var\log"
    app, dlg = _autofetch_on_dialog(root)
    dlg._machine_empty_retry_count = AUTO_FETCH_EMPTY_MACHINE_RETRY_MAX
    dlg._schedule_empty_machine_retry()
    assert dlg._machine_empty_retry_armed is False

    dlg._machine_empty_retry_count = 0
    dlg._schedule_empty_machine_retry()
    assert dlg._machine_empty_retry_armed is True

    _close_autofetch_dialog(app, dlg)


def test_empty_machine_retry_skips_after_late_success() -> None:
    """A load that succeeded between schedule and fire cancels the retry."""
    root = r"C:\Goldclub\var\log"
    app, dlg = _autofetch_on_dialog(root)
    calls: list[str] = []
    dlg._begin_cabinet_compare = lambda **kw: calls.append("compare")  # type: ignore[method-assign]
    dlg._queue_auto_fetch_refresh = lambda: calls.append("full_round")  # type: ignore[method-assign]
    dlg._compare_running = lambda: False  # type: ignore[method-assign]
    dlg._local_diff_running = False
    dlg._machine_state = {"coinin": "300"}
    dlg._machine_state_loaded = True
    dlg._loaded_cabinet_scan_root = root
    dlg._machine_state_loaded_at = 1_000.0  # fresh successful load
    dlg._machine_empty_retry_count = 3

    dlg._retry_empty_machine_reload()
    assert calls == []
    assert dlg._machine_empty_retry_count == 0

    _close_autofetch_dialog(app, dlg)


def test_keep_on_empty_arms_share_recovery_for_unc(monkeypatch) -> None:
    """A UNC root going empty starts the share watcher alongside the retry."""
    root = r"\\10.0.0.90\c$\Goldclub\var\log"
    monkeypatch.setattr(
        "network.goldclub_paths.should_arm_share_recovery_after_empty_load",
        lambda _sr: True,
    )
    app, dlg = _autofetch_on_dialog(root)
    dlg._machine_state = {"coinin": "250"}
    dlg._machine_state_loaded = True
    dlg._loaded_cabinet_scan_root = root
    dlg._machine_state_loaded_at = 50.0
    dlg._schedule_empty_machine_retry = lambda: None  # type: ignore[method-assign]
    dlg._update_prefetch_status = lambda *a, **k: None  # type: ignore[method-assign]

    kept = dlg._keep_machine_on_empty_reload()
    assert kept is True
    assert dlg._share_recovery_pending is True
    dlg._recovery_timer.stop()

    _close_autofetch_dialog(app, dlg)


def test_stale_local_diff_signal_is_dropped() -> None:
    """A late done() from a watchdog-released task must not touch the dialog."""
    root = r"C:\Goldclub\var\log"
    app, dlg = _autofetch_on_dialog(root)
    dlg._local_diff_job_id = 2  # a newer task is the current one
    dlg._local_diff_running = True
    dlg._machine_state = {"coinin": "111"}

    dlg._on_local_diff_done(
        "stale", {"sas": {}, "machine": {"coinin": "999"}, "__job__": 1}
    )
    assert dlg._local_diff_running is True
    assert dlg._machine_state == {"coinin": "111"}

    # The current task's signal still applies normally.
    dlg._render = lambda **kw: None  # type: ignore[method-assign]
    dlg._update_prefetch_status = lambda *a, **k: None  # type: ignore[method-assign]
    dlg._on_local_diff_done(
        "fresh", {"sas": {}, "machine": {"coinin": "999"}, "__job__": 2}
    )
    assert dlg._local_diff_running is False
    assert dlg._machine_state == {"coinin": "999"}

    _close_autofetch_dialog(app, dlg)

def test_apply_cabinet_state_drops_stale_scan_root() -> None:
    """A CompareWorker that finished after the edit box moved must not paint."""
    root_a = r"\\10.0.0.90\c$\Goldclub\var"
    root_b = r"\\10.0.0.171\c$\Goldclub\var"
    app, dlg = _autofetch_on_dialog(root_a)
    dlg._active_compare_job_id = 7
    dlg._compare_job_roots[7] = root_a
    blocked = dlg._scan_root_edit.blockSignals(True)
    dlg._scan_root_edit.setText(root_b)
    dlg._scan_root_edit.blockSignals(blocked)
    dlg._scan_root = root_b
    dlg._machine_state = {}
    painted: list[bool] = []
    dlg._run_cabinet_ui_refresh = lambda: painted.append(True)  # type: ignore[method-assign]
    dlg._release_compare_busy_ui_if_idle = lambda **kw: None  # type: ignore[method-assign]

    dlg._apply_cabinet_state({"coinin": "999"}, job_id=7)
    assert dlg._machine_state == {}
    assert painted == []
    assert 7 not in dlg._compare_job_roots

    _close_autofetch_dialog(app, dlg)


def test_worker_error_clears_force_pending_before_round_close() -> None:
    root = r"\\10.0.0.90\c$\Goldclub\var"
    app, dlg = _autofetch_on_dialog(root)
    dlg._active_compare_job_id = 3
    dlg._compare_job_roots[3] = root
    dlg._cabinet_compare_prefetch = True  # avoid modal QMessageBox in CI
    dlg._cabinet_compare_force_pending = True
    dlg._auto_fetch_round_resynced = True
    dlg._auto_fetch_round_active = True
    dlg._keep_machine_on_empty_reload = lambda: True  # type: ignore[method-assign]
    dlg._arm_share_recovery = lambda: None  # type: ignore[method-assign]
    dlg._prompt_local_d_scan_root = lambda **kw: None  # type: ignore[method-assign]
    dlg._render = lambda **kw: None  # type: ignore[method-assign]
    dlg._update_prefetch_status = lambda *a, **k: None  # type: ignore[method-assign]
    dlg._meter_fetch_running = lambda: False  # type: ignore[method-assign]
    dlg._compare_running = lambda: False  # type: ignore[method-assign]
    dlg._local_diff_running = False

    dlg._on_worker_error("cabinet XML missing", job_id=3)
    assert dlg._cabinet_compare_force_pending is False
    assert dlg._auto_fetch_round_resynced is False
    assert dlg._auto_fetch_round_active is False

    _close_autofetch_dialog(app, dlg)


def test_local_pair_wait_gives_up_when_nothing_writes() -> None:
    """Silent folders must not spin the 150 ms queue forever."""
    import time

    from gui.sas_verify_dialog import AUTO_FETCH_LOCAL_PAIR_WAIT_S

    root = r"C:\Goldclub\var\log"
    app, dlg = _autofetch_on_dialog(root)
    dlg._auto_fetch_refresh_queued = True
    dlg._auto_fetch_pair_wait_started_mono = (
        time.monotonic() - AUTO_FETCH_LOCAL_PAIR_WAIT_S - 0.1
    )
    dlg._auto_fetch_source_mtimes = {"gm2au": 1.0, "SASControler1": 1.0}
    dlg._local_diff_running = False
    dlg._compare_running = lambda: False  # type: ignore[method-assign]

    import network.accounting_state_loader as loader

    orig = loader.device_source_mtimes
    loader.device_source_mtimes = lambda _sr: {  # type: ignore[assignment]
        "gm2au": 1.0,
        "SASControler1": 1.0,
    }
    try:
        dlg._run_queued_local_pair_refresh()
    finally:
        loader.device_source_mtimes = orig  # type: ignore[assignment]

    assert dlg._auto_fetch_refresh_queued is False
    assert dlg._auto_fetch_pair_wait_started_mono == 0.0
    assert not dlg._auto_fetch_queue_timer.isActive()

    _close_autofetch_dialog(app, dlg)


def test_local_pair_wait_starts_clock_when_saw_is_false() -> None:
    root = r"C:\Goldclub\var\log"
    app, dlg = _autofetch_on_dialog(root)
    dlg._auto_fetch_refresh_queued = True
    dlg._auto_fetch_pair_wait_started_mono = 0.0
    dlg._auto_fetch_source_mtimes = {"gm2au": 1.0, "SASControler1": 1.0}
    dlg._local_diff_running = False
    dlg._compare_running = lambda: False  # type: ignore[method-assign]

    import network.accounting_state_loader as loader

    orig = loader.device_source_mtimes
    loader.device_source_mtimes = lambda _sr: {  # type: ignore[assignment]
        "gm2au": 1.0,
        "SASControler1": 1.0,
    }
    try:
        dlg._run_queued_local_pair_refresh()
    finally:
        loader.device_source_mtimes = orig  # type: ignore[assignment]

    assert dlg._auto_fetch_pair_wait_started_mono > 0.0
    assert dlg._auto_fetch_refresh_queued is True

    _close_autofetch_dialog(app, dlg)

