from __future__ import annotations


def test_find_running_sas_com_blockers_uses_hidden_single_tasklist(monkeypatch) -> None:
    import network.sas_serial_meters as m

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

        class R:
            # CSV image names — exact match only (lsass must not count as SAS*).
            stdout = (
                '"notepad.exe","1","Console","1","1 K"\n'
                '"lsass.exe","980","Services","0","18 K"\n'
                '"SASTest.exe","2222","Console","1","2 K"\n'
            )
            returncode = 0

        return R()

    monkeypatch.setattr(m, "_win_hidden_run", fake_run)
    monkeypatch.setattr(m, "_blocker_cache_valid", False)
    found = m.find_running_sas_com_blockers(force_refresh=True)
    assert found == ["SASTest.exe"]
    assert len(calls) == 1
    assert calls[0][:1] == ["tasklist"]
    assert "/FO" in calls[0]
    found2 = m.find_running_sas_com_blockers()
    assert found2 == ["SASTest.exe"]
    assert len(calls) == 1


def test_tasklist_image_names_do_not_substring_match_lsass() -> None:
    from network.sas_serial_meters import _tasklist_image_names, sas_com_blocker_process_names

    names = _tasklist_image_names(
        '"lsass.exe","980","Services","0","18 K"\n'
        '"chrome.exe","1","Console","1","1 K"\n'
    )
    assert "lsass.exe" in names
    for exe in sas_com_blocker_process_names():
        assert exe.casefold() not in names


def test_tasklist_failure_keeps_last_known_blockers(monkeypatch) -> None:
    """A broken tasklist probe must not flip an armed recovery to 'no blockers'."""
    import network.sas_serial_meters as m

    def good_run(cmd, **kwargs):
        class R:
            stdout = '"SASTest.exe","2222","Console","1","2 K"\n'
            returncode = 0

        return R()

    monkeypatch.setattr(m, "_win_hidden_run", good_run)
    monkeypatch.setattr(m, "_blocker_cache_valid", False)
    assert m.find_running_sas_com_blockers(force_refresh=True) == ["SASTest.exe"]

    def broken_run(cmd, **kwargs):
        raise OSError("tasklist unavailable")

    monkeypatch.setattr(m, "_win_hidden_run", broken_run)
    # Failure returns the last known verdict instead of caching [] as truth.
    assert m.find_running_sas_com_blockers(force_refresh=True) == ["SASTest.exe"]

    # With no prior verdict at all, a failure reports nothing (and caches nothing).
    monkeypatch.setattr(m, "_blocker_cache_valid", False)
    monkeypatch.setattr(m, "_blocker_cache_value", [])
    assert m.find_running_sas_com_blockers(force_refresh=True) == []
    assert m._blocker_cache_valid is False


def test_cached_only_probe_never_spawns_tasklist(monkeypatch) -> None:
    """Auto fetch reads the cache; a background round must not pay for tasklist."""
    import network.sas_serial_meters as m

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

        class R:
            stdout = '"SASTest.exe","2222","Console","1","2 K"\n'
            returncode = 0

        return R()

    monkeypatch.setattr(m, "_win_hidden_run", fake_run)
    monkeypatch.setattr(m.sys, "platform", "win32")

    # No verdict yet: report nothing rather than enumerate processes.
    monkeypatch.setattr(m, "_blocker_cache_valid", False)
    monkeypatch.setattr(m, "_blocker_cache_value", [])
    assert m.find_running_sas_com_blockers(cached_only=True) == []
    assert calls == []

    # A stale verdict is reused, still without spawning tasklist.
    monkeypatch.setattr(m, "_blocker_cache_valid", True)
    monkeypatch.setattr(m, "_blocker_cache_value", ["SASTest.exe"])
    monkeypatch.setattr(m, "_blocker_cache_at", 0.0)
    assert m.find_running_sas_com_blockers(cached_only=True) == ["SASTest.exe"]
    assert calls == []

    # Without the flag the same stale cache does refresh.
    assert m.find_running_sas_com_blockers() == ["SASTest.exe"]
    assert len(calls) == 1


def test_win_hidden_run_sets_create_no_window(monkeypatch) -> None:
    import subprocess
    import network.sas_serial_meters as m

    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        class R:
            stdout = ""
            returncode = 0
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(m.sys, "platform", "win32")
    m._win_hidden_run(["tasklist", "/NH"], timeout=1.0)
    assert seen.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_igt_sas_tester_aliases_are_blockers() -> None:
    from network.sas_serial_meters import sas_com_blocker_process_names

    names = {n.casefold() for n in sas_com_blocker_process_names()}
    assert "igtsastester.exe" in names
    assert "sastester.exe" in names
    assert "sastestx_cwh.exe" in names


def test_sastest_prefix_matches_igt_sas_test_program(monkeypatch) -> None:
    import network.sas_serial_meters as m

    def fake_run(cmd, **kwargs):
        class R:
            stdout = '"sastestx_cwh.exe","25660","Console","1","24 K"\n'
            returncode = 0

        return R()

    monkeypatch.setattr(m, "_win_hidden_run", fake_run)
    monkeypatch.setattr(m, "_blocker_cache_valid", False)
    found = m.find_running_sas_com_blockers(force_refresh=True)
    assert found == ["sastestx_cwh.exe"]


def test_sas_com_blocker_cache_ready_flag(monkeypatch) -> None:
    import network.sas_serial_meters as m

    monkeypatch.setattr(m, "_blocker_cache_valid", False)
    assert m.sas_com_blocker_cache_ready() is False
    monkeypatch.setattr(m, "_blocker_cache_valid", True)
    assert m.sas_com_blocker_cache_ready() is True


def test_begin_meter_fetch_tries_com_even_when_named_blocker_running(
    monkeypatch,
) -> None:
    """Process names do not abort capture — Access Denied from open is truth."""
    from types import SimpleNamespace

    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    import network.sas_serial_meters as meters
    from gui import sas_verify_dialog as svd
    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        meters,
        "find_running_sas_com_blockers",
        lambda **kw: ["SASTest.exe"],
    )
    monkeypatch.setattr(meters, "sas_com_blocker_cache_ready", lambda: True)

    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"\\10.0.0.90\c$\Goldclub\var\log",
    )
    dlg._resolved_com_port_for_fetch = lambda: "COM4"  # type: ignore[method-assign]
    dlg._local_files_only_mode = lambda: False  # type: ignore[method-assign]
    dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr(svd.MeterFetchWorker, "run", lambda self: None)
    assert dlg._begin_meter_fetch(prefetch=True, force=True) is True
    assert dlg._meter_fetch_thread is not None
    assert dlg._cabinet_share_only_mode is False
    dlg.deleteLater()
    app.processEvents()


def test_access_denied_error_offers_cabinet_share(monkeypatch) -> None:
    """Share dialog is gated on COM Access Denied, not process names."""
    from types import SimpleNamespace

    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    dlg = SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=r"\\10.0.0.90\c$\Goldclub\var\log",
    )
    pair_calls = {"n": 0}
    offered = {"kw": None}

    def fake_offer(**kw):
        offered["kw"] = kw
        dlg._enable_cabinet_share_only_mode()
        return True

    dlg._offer_cabinet_share_when_com_blocked = fake_offer  # type: ignore[method-assign]
    dlg._begin_local_pair_diff = lambda: pair_calls.__setitem__(  # type: ignore[method-assign]
        "n", pair_calls["n"] + 1
    )
    # Share fallback is offered only while an IGT SAS tester holds the port.
    monkeypatch.setattr(
        "gui.sas_verify_dialog.igt_sas_tester_is_running",
        lambda force_refresh=False: True,
    )
    dlg._meter_fetch_prefetch = True
    dlg._on_meter_fetch_error(
        "Could not open COM4: PermissionError(13, 'Access is denied.', None, 5)"
    )
    assert offered["kw"] is not None
    assert offered["kw"].get("port_busy") is True
    assert dlg._cabinet_share_only_mode is True
    assert dlg._sas_capture_expected() is False
    assert pair_calls["n"] >= 1
    dlg.deleteLater()
    app.processEvents()


def test_meter_fetch_worker_surfaces_access_denied_not_process_name(
    monkeypatch,
) -> None:
    """Named blockers do not short-circuit — serial Access Denied is reported."""
    from gui.sas_verify_dialog import MeterFetchWorker
    import network.sas_serial_meters as meters

    monkeypatch.setattr(
        meters,
        "find_running_sas_com_blockers",
        lambda **kw: ["SASHost.exe"],
    )
    monkeypatch.setattr(meters, "sas_com_blocker_cache_ready", lambda: True)

    def boom(**kwargs):
        raise RuntimeError(
            "Could not open COM4 for SAS meter fetch.\n"
            "PermissionError(13, 'Access is denied.', None, 5)"
        )

    monkeypatch.setattr(meters, "fetch_meters_over_serial", boom)

    errs: list[str] = []
    done: list[object] = []
    w = MeterFetchWorker(com_port="COM4", com_baud=19200, prefetch=True, warm=False)
    w.error.connect(errs.append)
    w.finished.connect(done.append)
    w.run()
    assert done == []
    assert errs and "Access is denied" in errs[0]
