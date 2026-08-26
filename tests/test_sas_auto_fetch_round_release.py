"""An Auto fetch round must close itself, never wait for the 90 s watchdog.

The post-capture Machine re-read latches ``_auto_fetch_round_resynced``, which
blocks ``_auto_fetch_round_may_close()``. ``_begin_cabinet_compare`` has several
early exits, so a resync that never started used to hold the round open with
every latch clear — the table froze on SYNCING and the log filled with
"auto-fetch round stale after 90s - releasing latches (... all False)".
"""

from __future__ import annotations

import time
from types import SimpleNamespace

_ALIASES = {"0000": "coinin", "0001": "coinout"}


def _fake_vm() -> SimpleNamespace:
    return SimpleNamespace(
        current_product_name="GUI-Test",
        get_gm2u_value_for_sas_code=lambda code, state: state.get(
            _ALIASES.get((code or "").upper(), ""), ""
        ),
    )


def _dialog_mid_round():
    """Cabinet root with Auto fetch on and a round open, as after a COM capture."""
    from PySide6.QtCore import QThreadPool

    from gui.sas_verify_dialog import SasVerifyDialog

    root = r"\\10.0.0.90\c$\Goldclub\var\log"
    dlg = SasVerifyDialog(_fake_vm(), QThreadPool.globalInstance(), scan_root=root)
    dlg._prefetch_started = True
    dlg._reload_game_theme_catalog = lambda: None  # type: ignore[method-assign]
    blocked = dlg._auto_fetch_toggle.blockSignals(True)
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._auto_fetch_toggle.blockSignals(blocked)
    blocked = dlg._scan_root_edit.blockSignals(True)
    dlg._scan_root_edit.setText(root)
    dlg._scan_root_edit.blockSignals(blocked)
    dlg._scan_root = root
    dlg._machine_state = {"coinin": "1000", "coinout": "50"}
    dlg._machine_state_loaded = True
    dlg._loaded_cabinet_scan_root = root
    dlg._machine_state_loaded_at = time.monotonic()
    dlg._auto_fetch_round_active = True
    dlg._auto_fetch_round_resynced = False
    dlg._auto_fetch_round_started_mono = time.monotonic()
    return dlg


def test_round_releases_when_the_paired_reread_cannot_start() -> None:
    """A resync that starts no load and queues none must settle the round now."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_mid_round()
    # Stand in for any _begin_cabinet_compare early exit (no scan root, a load
    # in flight it declines to restart): nothing started, nothing queued.
    dlg._begin_cabinet_compare = lambda **_kw: None  # type: ignore[method-assign]

    dlg._resync_machine_after_auto_fetch_capture()
    assert dlg._auto_fetch_round_resynced is True  # latched until the read lands
    app.processEvents()  # let the deferred resync run

    assert dlg._auto_fetch_round_active is False
    assert dlg._auto_fetch_round_resynced is False
    assert dlg._auto_fetch_round_started_mono == 0.0
    assert dlg._compare_sources_settled() is True

    dlg.deleteLater()
    app.processEvents()


def test_round_stays_open_while_the_paired_reread_is_in_flight() -> None:
    """The whole point of the latch: don't settle before the paired read lands."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_mid_round()
    started: list[bool] = []

    def _fake_begin(**_kw):
        started.append(True)

    dlg._begin_cabinet_compare = _fake_begin  # type: ignore[method-assign]
    dlg._compare_running = lambda: bool(started)  # type: ignore[method-assign]

    dlg._resync_machine_after_auto_fetch_capture()
    app.processEvents()

    assert started == [True]
    assert dlg._auto_fetch_round_active is True
    assert dlg._auto_fetch_round_resynced is True
    assert dlg._compare_sources_settled() is False

    dlg.deleteLater()
    app.processEvents()


def test_round_stays_open_while_a_forced_reread_is_queued() -> None:
    """A declined restart leaves force_pending set; that read is still coming."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_mid_round()

    def _fake_begin(**_kw):
        dlg._cabinet_compare_force_pending = True

    dlg._begin_cabinet_compare = _fake_begin  # type: ignore[method-assign]

    dlg._resync_machine_after_auto_fetch_capture()
    app.processEvents()

    assert dlg._auto_fetch_round_active is True
    assert dlg._auto_fetch_round_resynced is True

    dlg.deleteLater()
    app.processEvents()


def test_ending_a_round_clears_the_round_clock() -> None:
    """A live clock on a closed round makes the watchdog fire against nothing."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_mid_round()

    dlg._end_auto_fetch_round(repaint=False)
    assert dlg._auto_fetch_round_active is False
    assert dlg._auto_fetch_round_started_mono == 0.0

    # A latch surviving a closed round would block the next round's close.
    dlg._auto_fetch_round_resynced = True
    dlg._auto_fetch_round_started_mono = time.monotonic()
    dlg._end_auto_fetch_round(repaint=False)
    assert dlg._auto_fetch_round_resynced is False
    assert dlg._auto_fetch_round_started_mono == 0.0

    dlg.deleteLater()
    app.processEvents()


def test_idle_round_settles_on_a_timer_tick_instead_of_waiting_90s() -> None:
    """A round with no worker left must close in seconds, not at the stale cap.

    On the cabinet this held every verdict at SYNCING for 90 s, which also
    postponed the changed-row highlight until long after the meter moved.
    """
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import (
        AUTO_FETCH_ROUND_IDLE_S,
        AUTO_FETCH_ROUND_STALE_S,
    )

    assert AUTO_FETCH_ROUND_IDLE_S < AUTO_FETCH_ROUND_STALE_S

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_mid_round()
    # Every worker finished, yet the round is still open: the wedge seen live.
    dlg._local_diff_running = False
    dlg._cabinet_compare_force_pending = False
    dlg._compare_running = lambda: False  # type: ignore[method-assign]
    dlg._meter_fetch_running = lambda: False  # type: ignore[method-assign]
    assert dlg._auto_fetch_round_idle() is True

    # Too young to judge — a worker may still be about to start.
    dlg._auto_fetch_round_started_mono = time.monotonic()
    dlg._on_auto_fetch_timer()
    assert dlg._auto_fetch_round_active is True

    dlg._auto_fetch_round_started_mono = time.monotonic() - (AUTO_FETCH_ROUND_IDLE_S + 0.5)
    dlg._on_auto_fetch_timer()
    assert dlg._auto_fetch_round_active is False
    assert dlg._compare_sources_settled() is True

    dlg.deleteLater()
    app.processEvents()


def test_idle_settle_leaves_a_working_round_alone() -> None:
    """A round with a worker in flight must survive the idle check."""
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import AUTO_FETCH_ROUND_IDLE_S

    app = QApplication.instance() or QApplication([])
    for attr, setup in (
        ("local_diff", lambda d: setattr(d, "_local_diff_running", True)),
        (
            "force_pending",
            lambda d: setattr(d, "_cabinet_compare_force_pending", True),
        ),
        ("compare", lambda d: setattr(d, "_compare_running", lambda: True)),
        ("meter_fetch", lambda d: setattr(d, "_meter_fetch_running", lambda: True)),
    ):
        dlg = _dialog_mid_round()
        dlg._local_diff_running = False
        dlg._cabinet_compare_force_pending = False
        dlg._compare_running = lambda: False  # type: ignore[method-assign]
        dlg._meter_fetch_running = lambda: False  # type: ignore[method-assign]
        setup(dlg)
        dlg._auto_fetch_round_started_mono = time.monotonic() - (
            AUTO_FETCH_ROUND_IDLE_S + 0.5
        )
        assert dlg._auto_fetch_round_idle() is False, attr
        dlg._on_auto_fetch_timer()
        assert dlg._auto_fetch_round_active is True, attr
        dlg.deleteLater()
        app.processEvents()


def test_stale_watchdog_does_not_fire_on_a_closed_round() -> None:
    """End to end: the closed round leaves nothing for the 90 s watchdog to find."""
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import AUTO_FETCH_ROUND_STALE_S

    app = QApplication.instance() or QApplication([])
    dlg = _dialog_mid_round()
    dlg._begin_cabinet_compare = lambda **_kw: None  # type: ignore[method-assign]
    dlg._resync_machine_after_auto_fetch_capture()
    app.processEvents()

    # Pretend the watchdog tick arrives long after the round ended.
    warnings: list[str] = []
    dlg._auto_fetch_round_started_mono = time.monotonic() - (
        AUTO_FETCH_ROUND_STALE_S + 5.0
    )
    dlg._end_auto_fetch_round = lambda **_kw: warnings.append(  # type: ignore[method-assign]
        "released"
    )
    dlg._on_auto_fetch_timer()

    assert warnings == []

    dlg.deleteLater()
    app.processEvents()
