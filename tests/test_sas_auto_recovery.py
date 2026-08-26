"""COM/share auto-recovery and the Help menu."""

from __future__ import annotations

from types import SimpleNamespace

from gui.sas_verify_dialog import (
    SAS_VERIFY_HELP_HTML,
    CABINET_SHARE_FETCH_BUTTON_LABEL,
    CABINET_SHARE_WAIT_COM_BUTTON_LABEL,
    build_com_blocked_cabinet_share_dialog,
    cabinet_share_only_status,
    cabinet_share_when_com_blocked_prompt_text,
    com_access_denied_online_status,
    com_error_is_link_dead,
    com_error_is_port_busy,
    com_recovery_waiting_status,
    game_recovery_waiting_status,
    local_diff_summary_text,
    local_files_only_status,
    share_error_is_access,
    share_recovery_waiting_status,
    should_offer_cabinet_share_when_com_blocked,
    should_offer_g_drive_prompt,
    should_offer_local_scan_prompt,
    local_g_drive_waiting_for_game_status,
)


def test_com_error_is_port_busy_matches_real_permission_error() -> None:
    msg = (
        "Could not open COM4 for SAS meter fetch.\n"
        "could not open port 'COM4': PermissionError(13, 'Access is denied', None, 5)"
    )
    assert com_error_is_port_busy(msg)
    assert com_error_is_port_busy("COM4 looks occupied by SASHost.exe")
    assert com_error_is_port_busy("COM4 is in use (igtSASTester.exe)")
    assert not com_error_is_port_busy("SAS link not responding")
    assert not com_error_is_port_busy("")


def test_share_error_is_access() -> None:
    assert share_error_is_access("Access is denied")
    assert share_error_is_access("System error 5 has occurred")
    assert share_error_is_access("Connection Failed: SMB probe timeout")
    assert share_error_is_access("[WinError 53] The network path was not found")
    assert not share_error_is_access("XML parse failed")
    assert not share_error_is_access("")


def test_recovery_waiting_status_texts() -> None:
    com_txt = com_recovery_waiting_status("COM4")
    assert "COM4" in com_txt and "retries automatically" in com_txt
    assert "the SAS COM port" in com_recovery_waiting_status("")

    share_txt = share_recovery_waiting_status(r"\\10.0.0.90\c$\Goldclub\var", "10.0.0.90")
    assert "registered automatically" in share_txt
    assert "cmdkey /add:10.0.0.90" in share_txt
    assert "GOLD-CLUB\\test" in share_txt
    assert "reloads automatically" in share_txt
    assert "<cabinet-ip>" in share_recovery_waiting_status("", "")


def test_help_html_mentions_key_fixes() -> None:
    assert "IGT SAS tester" in SAS_VERIFY_HELP_HTML
    assert "SASControler" in SAS_VERIFY_HELP_HTML
    assert "10.0.0.90" in SAS_VERIFY_HELP_HTML
    assert "registered automatically" in SAS_VERIFY_HELP_HTML
    assert "cmdkey /add:" in SAS_VERIFY_HELP_HTML
    assert "Auto fetch" in SAS_VERIFY_HELP_HTML
    assert "DeviceManagerData.xml" in SAS_VERIFY_HELP_HTML
    assert "Always on top" in SAS_VERIFY_HELP_HTML
    assert "ruleta\\var" in SAS_VERIFY_HELP_HTML
    assert "Open Machine source file" in SAS_VERIFY_HELP_HTML
    assert "SYNCING" in SAS_VERIFY_HELP_HTML
    assert "soft pulse" in SAS_VERIFY_HELP_HTML
    # Monitor + hotkey features (documented in a Keyboard shortcuts section).
    assert "Move to Monitor 2" in SAS_VERIFY_HELP_HTML
    assert "Keyboard shortcuts" in SAS_VERIFY_HELP_HTML
    assert "Ctrl+Alt+Shift+M" in SAS_VERIFY_HELP_HTML
    assert "Ctrl+Alt+Shift+K" in SAS_VERIFY_HELP_HTML
    assert "Ctrl+Alt+Shift+T" in SAS_VERIFY_HELP_HTML
    assert "Ctrl+Tab" in SAS_VERIFY_HELP_HTML
    assert "Ctrl+1" in SAS_VERIFY_HELP_HTML
    assert "What this tool is for" in SAS_VERIFY_HELP_HTML
    assert "Handpay In" in SAS_VERIFY_HELP_HTML
    assert "0023" in SAS_VERIFY_HELP_HTML
    assert "handpay*InAmt" in SAS_VERIFY_HELP_HTML
    assert "Refresh Meters" in SAS_VERIFY_HELP_HTML
    assert "No EGM reboot required" in SAS_VERIFY_HELP_HTML
    assert "you do not need to" in SAS_VERIFY_HELP_HTML
    assert "power-cycle or reboot" in SAS_VERIFY_HELP_HTML


def _make_dialog():
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import (
        _KEY_AUTO_FETCH,
        SasVerifyDialog,
        _sas_verify_settings,
    )

    s = _sas_verify_settings()
    s.remove(f"sasVerify/{_KEY_AUTO_FETCH}")
    s.sync()

    app = QApplication.instance() or QApplication([])
    vm = SimpleNamespace(current_product_name="GUI-Test")
    dlg = SasVerifyDialog(vm, QThreadPool.globalInstance(), scan_root="")
    dlg._prefetch_started = True  # avoid COM/cabinet prefetch side effects in CI
    # Default Auto fetch is checked but not armed until show — keep it off in
    # unit tests so processEvents cannot start a real cabinet/COM round.
    # blockSignals: setChecked(False) would otherwise persist auto_fetch=false.
    blocked = dlg._auto_fetch_toggle.blockSignals(True)
    dlg._auto_fetch_toggle.setChecked(False)
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
    return app, dlg


def _close_dialog(app, dlg) -> None:
    """Safe teardown: drop worker signals, stop timers/threads, restore pref."""
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


def test_should_offer_local_scan_prompt_gates_igt_and_online_hold() -> None:
    # COM down, IGT not running, not port-busy → may offer D:\ / USB.
    assert should_offer_local_scan_prompt(
        mux_detail="No SAS/MUX COM port detected", igt_running=False
    )
    # IGT running → wait for close, no prompt.
    assert not should_offer_local_scan_prompt(
        mux_detail="COM4 looks occupied by SASHost.exe", igt_running=True
    )
    # Port busy without IGT → online/host → access denied, no prompt.
    assert not should_offer_local_scan_prompt(
        mux_detail="PermissionError(13, 'Access is denied', None, 5)",
        igt_running=False,
    )


def test_should_offer_cabinet_share_when_com_blocked() -> None:
    # Access Denied + remote UNC is the only gate (not process names).
    assert should_offer_cabinet_share_when_com_blocked(
        has_remote_unc=True,
        port_busy=True,
    )
    assert not should_offer_cabinet_share_when_com_blocked(
        has_remote_unc=True,
        port_busy=False,
    )
    assert not should_offer_cabinet_share_when_com_blocked(
        has_remote_unc=False,
        port_busy=True,
    )



def test_cabinet_share_when_com_blocked_prompt_text() -> None:
    body = cabinet_share_when_com_blocked_prompt_text(
        scan_root=r"\\10.0.0.90\c$\Goldclub\var",
        cabinet_ip="10.0.0.90",
        com_detail="Access is denied",
    )
    assert "Access Denied" in body
    assert "Access is denied" in body
    assert "SASControler1" in body
    assert "cabinet share" in body.lower()
    assert "gm2au" in body
    status = cabinet_share_only_status(r"\\10.0.0.90\c$\Goldclub\var")
    assert "COM blocked" in status
    assert "SASControler" in status
    assert "no live SAS/MUX" in status


def test_should_offer_g_drive_prompt_requires_local_game_client() -> None:
    # Same COM/IGT gates as local scan, plus local EGM client must be up.
    assert should_offer_g_drive_prompt(
        mux_detail="No SAS/MUX COM port detected",
        igt_running=False,
        local_game_running=True,
    )
    assert not should_offer_g_drive_prompt(
        mux_detail="No SAS/MUX COM port detected",
        igt_running=False,
        local_game_running=False,
    )
    assert not should_offer_g_drive_prompt(
        mux_detail="PermissionError(13, 'Access is denied', None, 5)",
        igt_running=False,
        local_game_running=True,
    )
    assert not should_offer_g_drive_prompt(
        mux_detail="No SAS/MUX COM port detected",
        igt_running=True,
        local_game_running=True,
    )


def test_local_g_drive_waiting_for_game_status() -> None:
    txt = local_g_drive_waiting_for_game_status(r"G:\var\log")
    assert r"G:\var\log" in txt
    assert "OneHand.exe" in txt
    assert "godot.exe" in txt


def test_dialog_schedule_local_scan_prefers_g_when_game_up(monkeypatch) -> None:
    app, dlg = _make_dialog()
    offers: list[str] = []
    prompts: list[dict] = []
    dlg._offer_local_game_image_root = lambda c: offers.append(c)
    dlg._prompt_local_d_scan_root = lambda **kw: prompts.append(kw)
    monkeypatch.setattr(
        "network.goldclub_paths.discover_local_game_image_scan_root",
        lambda: r"G:\var\log",
    )
    monkeypatch.setattr(
        "network.health_monitor.local_egm_game_client_running",
        lambda: True,
    )
    monkeypatch.setattr(
        "network.health_monitor.is_running_on_local_egm",
        lambda: True,
    )
    dlg._schedule_local_scan_fallback(
        reason="no com",
        mux_detail="No SAS/MUX COM port detected",
        igt_running=False,
    )
    app.processEvents()
    assert offers == [r"G:\var\log"]
    assert prompts == []
    _close_dialog(app, dlg)


def test_dialog_schedule_local_scan_waits_on_egm_without_game(monkeypatch) -> None:
    app, dlg = _make_dialog()
    offers: list[str] = []
    prompts: list[dict] = []
    dlg._offer_local_game_image_root = lambda c: offers.append(c)
    dlg._prompt_local_d_scan_root = lambda **kw: prompts.append(kw)
    monkeypatch.setattr(
        "network.goldclub_paths.discover_local_game_image_scan_root",
        lambda: r"G:\var\log",
    )
    monkeypatch.setattr(
        "network.health_monitor.local_egm_game_client_running",
        lambda: False,
    )
    monkeypatch.setattr(
        "network.health_monitor.is_running_on_local_egm",
        lambda: True,
    )
    dlg._schedule_local_scan_fallback(
        reason="no com",
        mux_detail="No SAS/MUX COM port detected",
        igt_running=False,
    )
    app.processEvents()
    assert offers == []
    assert prompts == []
    assert "not running" in dlg._prefetch_status_label.text().lower()
    _close_dialog(app, dlg)


def test_com_access_denied_online_status() -> None:
    txt = com_access_denied_online_status("COM4")
    assert "Access denied" in txt
    assert "COM4" in txt
    assert "No IGT SAS tester" in txt
    assert "Auto-recovery armed" not in txt


def test_dialog_com_recovery_arms_and_recaptures(monkeypatch) -> None:
    app, dlg = _make_dialog()
    calls: list[tuple] = []
    dlg._begin_cabinet_compare = lambda **kw: calls.append(("compare", kw))
    dlg._prompt_local_d_scan_root = lambda **kw: calls.append(("prompt", kw))
    dlg._begin_meter_fetch = lambda **kw: calls.append(("fetch", kw))
    monkeypatch.setattr(
        "gui.sas_verify_dialog.igt_sas_tester_is_running",
        lambda force_refresh=False: True,
    )

    dlg._meter_fetch_prefetch = True  # prefetch path: no modal warning box
    dlg._on_meter_fetch_error(
        "could not open port 'COM4': PermissionError(13, 'Access is denied', None, 5)"
    )
    assert dlg._com_recovery_pending
    assert dlg._recovery_timer.isActive()
    assert "held by another app" in dlg._prefetch_status_label.text()

    # Port freed -> recapture is scheduled after a settle delay, then runs with
    # full IGT-like timing (the short sync budget right after the tester exits is
    # what used to read only idle bytes).
    dlg._on_recovery_probe_done(True, False)
    assert not dlg._com_recovery_pending
    assert calls == []  # not immediate — settle first
    assert "full SAS timing" in dlg._prefetch_status_label.text()
    dlg._restart_capture_after_com_freed()
    assert ("fetch", {"prefetch": True, "force": True, "full_timing": True}) in calls
    assert not dlg._recovery_timer.isActive()

    # Successful capture clears any leftover pending flag.
    dlg._com_recovery_pending = True
    dlg._apply_meter_fetch_complete(None)
    assert not dlg._com_recovery_pending

    _close_dialog(app, dlg)


def test_dialog_port_busy_without_igt_is_access_denied(monkeypatch) -> None:
    app, dlg = _make_dialog()
    prompts: list[dict] = []
    dlg._prompt_local_d_scan_root = lambda **kw: prompts.append(kw)
    dlg._offer_local_game_image_root = lambda c: prompts.append({"g": c})
    monkeypatch.setattr(
        "gui.sas_verify_dialog.igt_sas_tester_is_running",
        lambda force_refresh=False: False,
    )

    dlg._meter_fetch_prefetch = True
    dlg._scan_root = ""
    # Stale latch from a prior misdetect must not keep blaming IGT.
    dlg._com_recovery_pending = True
    dlg._recovery_timer.start()
    dlg._on_meter_fetch_error(
        "could not open port 'COM4': PermissionError(13, 'Access is denied', None, 5)"
    )
    assert not dlg._com_recovery_pending
    assert not dlg._recovery_timer.isActive()
    assert prompts == []
    assert "Access denied" in dlg._prefetch_status_label.text()
    assert "No IGT SAS tester" in dlg._prefetch_status_label.text()
    assert "Auto-recovery armed" not in dlg._prefetch_status_label.text()

    _close_dialog(app, dlg)


def test_recovery_recapture_pairs_with_machine_resync() -> None:
    """A capture landing after COM recovery must re-read Machine, not settle
    fresh SAS values against the older snapshot (transient false MISMATCH)."""
    app, dlg = _make_dialog()
    blocked = dlg._auto_fetch_toggle.blockSignals(True)
    dlg._auto_fetch_toggle.setChecked(True)
    dlg._auto_fetch_toggle.blockSignals(blocked)
    dlg._auto_fetch_timer.stop()
    dlg._scan_root = r"\\10.0.0.90\c$\Goldclub\var\log"
    resyncs: list[bool] = []
    dlg._resync_machine_after_auto_fetch_capture = (  # type: ignore[method-assign]
        lambda: resyncs.append(True)
    )
    dlg._apply_meter_fetch_result = lambda r: False  # type: ignore[method-assign]
    dlg._flush_pending_cabinet_ui_refresh = lambda: None  # type: ignore[method-assign]
    dlg._machine_loaded_for_current_root = lambda: True  # type: ignore[method-assign]
    dlg._update_prefetch_status = lambda *a, **k: None  # type: ignore[method-assign]

    # The recovery recapture path arms the pairing flag...
    fetches: list[dict] = []
    dlg._begin_meter_fetch = lambda **kw: fetches.append(kw) or True  # type: ignore[method-assign]
    dlg._restart_capture_after_com_freed()
    assert dlg._recovery_recapture_pending is True
    assert fetches and fetches[0].get("full_timing") is True

    # ...and completion outside a round opens one and pairs Machine.
    dlg._auto_fetch_round_active = False
    dlg._apply_meter_fetch_complete(None)
    assert resyncs == [True]
    assert dlg._auto_fetch_round_active is True
    assert dlg._recovery_recapture_pending is False

    dlg._auto_fetch_round_active = False
    dlg._auto_fetch_toggle.setChecked(False)
    _close_dialog(app, dlg)


def test_access_denied_status_survives_with_machine_loaded(monkeypatch) -> None:
    """The curated 'No IGT SAS tester' text must be the final status even when
    Machine is already loaded (the bare recompute used to overwrite it)."""
    app, dlg = _make_dialog()
    monkeypatch.setattr(
        "gui.sas_verify_dialog.igt_sas_tester_is_running",
        lambda force_refresh=False: False,
    )
    root = r"\\10.0.0.90\c$\Goldclub\var\log"
    dlg._scan_root = root
    dlg._machine_state = {"coinin": "1"}
    dlg._machine_state_loaded = True
    dlg._loaded_cabinet_scan_root = root
    dlg._machine_state_loaded_at = 1_000.0
    dlg._meter_fetch_prefetch = True
    dlg._meter_prefetch_retried = True
    dlg._flush_pending_cabinet_ui_refresh = lambda: None  # type: ignore[method-assign]
    dlg._refresh_com_port_list = lambda **kw: None  # type: ignore[method-assign]
    dlg._update_onehand_warning_label = lambda *a, **k: None  # type: ignore[method-assign]

    dlg._on_meter_fetch_error(
        "could not open port 'COM4': PermissionError(13, 'Access is denied', None, 5)"
    )
    assert "No IGT SAS tester" in dlg._prefetch_status_label.text()

    _close_dialog(app, dlg)


def test_get_meters_modal_does_not_blame_igt_without_blocker(monkeypatch) -> None:
    """Manual Get Meters must not show the IGT Auto-recovery false positive."""
    from PySide6.QtWidgets import QMessageBox

    app, dlg = _make_dialog()
    boxes: list[str] = []
    monkeypatch.setattr(
        "gui.sas_verify_dialog.igt_sas_tester_is_running",
        lambda force_refresh=False: False,
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: boxes.append(str(args[2] if len(args) > 2 else kwargs.get("text", ""))),
    )
    dlg._meter_fetch_prefetch = False  # manual path → modal
    dlg._com_recovery_pending = True
    dlg._on_meter_fetch_error(
        "Could not open COM4: Access is denied."
    )
    assert boxes
    assert "Auto-recovery armed" not in boxes[0]
    assert "No IGT SAS tester" in boxes[0]
    assert not dlg._com_recovery_pending

    _close_dialog(app, dlg)


def test_dialog_share_recovery_arms_on_empty_unc_state(monkeypatch) -> None:
    app, dlg = _make_dialog()
    calls: list[tuple] = []
    dlg._begin_meter_fetch = lambda **kw: calls.append(("fetch", kw))
    dlg._scan_root = r"\\127.0.0.1\c$\Goldclub\var"
    monkeypatch.setattr(
        "network.goldclub_paths.should_arm_share_recovery_after_empty_load",
        lambda _sr: True,
    )

    # Empty machine state on a UNC root arms the share watcher.
    dlg._apply_cabinet_state({})
    assert dlg._share_recovery_pending
    assert dlg._recovery_timer.isActive()
    dlg._update_prefetch_status()
    assert "cmdkey /add:127.0.0.1" in dlg._prefetch_status_label.text()

    # Share reachable again -> one automatic Machine reload.
    dlg._begin_cabinet_compare = lambda **kw: calls.append(("compare", kw))
    dlg._on_recovery_probe_done(False, True)
    assert not dlg._share_recovery_pending
    assert dlg._share_recovery_reloading
    assert ("compare", {"prefetch": True, "force": True}) in calls

    # Recovery reload still empty (cabinet has no state) -> must NOT re-arm.
    dlg._apply_cabinet_state({})
    assert not dlg._share_recovery_pending
    assert not dlg._share_recovery_reloading

    # A later ordinary empty load arms it again.
    dlg._apply_cabinet_state({})
    assert dlg._share_recovery_pending

    # Successful load clears everything.
    dlg._apply_cabinet_state({"coinin": "5"})
    assert not dlg._share_recovery_pending
    assert not dlg._share_recovery_reloading

    _close_dialog(app, dlg)


def test_com_error_is_link_dead() -> None:
    assert com_error_is_link_dead("SAS link not responding on COM4")
    assert com_error_is_link_dead("No bytes were received")
    assert com_error_is_link_dead("The EGM/MUX returned no bytes")
    assert not com_error_is_link_dead("could not open port 'COM4': PermissionError")
    assert not com_error_is_link_dead("")


def test_game_recovery_waiting_status() -> None:
    txt = game_recovery_waiting_status("OneHand.exe", "10.0.0.171")
    assert "OneHand.exe" in txt and "10.0.0.171" in txt
    assert "automatically" in txt
    assert "the EGM" in game_recovery_waiting_status("", "")


def test_local_files_only_status_and_diff_summary() -> None:
    txt = local_files_only_status(r"G:\var\log\ruleta", "Local state folders agree (a vs b).")
    assert "not possible on the machine itself" in txt
    assert "only local" in txt.lower()
    assert "agree" in txt

    assert "no cross-check" in local_diff_summary_text(["gm2au"], {})
    ok = local_diff_summary_text(["gm2au", "SASControler1"], {})
    assert "agree" in ok and "SASControler1" in ok
    bad = local_diff_summary_text(
        ["gm2au", "SASControler1"], {"coinin": {"gm2au": "1", "SASControler1": "2"}}
    )
    assert "1 meter(s) differ" in bad and "coinin" in bad


def test_diff_machine_state_sources() -> None:
    from network.accounting_state_loader import diff_machine_state_sources

    # Fewer than two sources: nothing to diff.
    assert diff_machine_state_sources({}) == {}
    assert diff_machine_state_sources({"gm2au": {"coinin": "1"}}) == {}

    sources = {
        "gm2au": {"coinin": "3900000", "coinout": "4450000", "onlyhere": "7", "__currencyid__": "COP"},
        "SASControler1": {"coinin": "3900000", "coinout": "999", "__currencyid__": "EUR"},
    }
    diffs = diff_machine_state_sources(sources)
    assert "coinout" in diffs and diffs["coinout"]["SASControler1"] == "999"
    assert "coinin" not in diffs        # equal values
    assert "onlyhere" not in diffs      # present in one source only
    assert "__currencyid__" not in diffs  # reserved keys ignored

    # Numeric normalization: "126.00" == "126".
    assert diff_machine_state_sources(
        {"a": {"m": "126.00"}, "b": {"m": "126"}}
    ) == {}


def test_load_machine_state_sources_groups_by_folder(tmp_path, monkeypatch) -> None:
    import network.goldclub_paths as gp
    from network.accounting_state_loader import load_machine_state_sources

    state = tmp_path / "state"
    for folder, value in (("SASControler1", "10"), ("gm2au", "20")):
        d = state / folder
        d.mkdir(parents=True)
        (d / "DeviceManagerData.xml_1").write_text(
            f"""<?xml version="1.0"?>
<deviceManager xmlns:d4p1="http://x">
  <d4p1:perfMeter d4p1:meterName="coinIn" d4p1:meterValue="{value}"/>
</deviceManager>
""",
            encoding="utf-8",
        )
    layout = SimpleNamespace(state_gcmessenger=state)
    monkeypatch.setattr(gp, "resolve_goldclub_layout", lambda root: layout)

    sources = load_machine_state_sources(r"C:\whatever\Goldclub\var")
    assert sources["SASControler1"]["coinin"] == "10"
    assert sources["gm2au"]["coinin"] == "20"


def _set_scan_root_silently(dlg, root: str) -> None:
    dlg._scan_root = root
    blocked = dlg._scan_root_edit.blockSignals(True)
    dlg._scan_root_edit.setText(root)
    dlg._scan_root_edit.blockSignals(blocked)


def test_dialog_local_files_only_mode_and_mux_skip() -> None:
    app, dlg = _make_dialog()
    _set_scan_root_silently(dlg, r"G:\var\log\ruleta")
    assert dlg._local_files_only_mode()

    port, detail = dlg._sas_mux_accessible()
    assert port == ""
    assert "not possible" in detail

    # Self-UNC remaps to a local path; without a local Goldclub install /
    # on-EGM runtime it is still not files-only (COM capture stays available).
    _set_scan_root_silently(dlg, r"\\127.0.0.1\c$\Goldclub\var")
    assert not dlg._local_files_only_mode()

    # A remote cabinet UNC must never be files-only.
    _set_scan_root_silently(dlg, r"\\10.0.0.90\c$\Goldclub\var")
    assert not dlg._local_files_only_mode()

    _close_dialog(app, dlg)


def test_dialog_game_recovery_refetches_all_meters() -> None:
    app, dlg = _make_dialog()
    calls: list[tuple] = []
    dlg._begin_cabinet_compare = lambda **kw: calls.append(("compare", kw))
    dlg._begin_meter_fetch = lambda **kw: calls.append(("fetch", kw))
    _set_scan_root_silently(dlg, r"\\127.0.0.1\c$\Goldclub\var")

    dlg._arm_game_recovery()
    assert dlg._game_recovery_pending
    assert dlg._recovery_timer.isActive()
    dlg._update_prefetch_status()
    assert "127.0.0.1" in dlg._prefetch_status_label.text()
    assert "automatically" in dlg._prefetch_status_label.text()

    # Probe inconclusive (WinRM unreachable): no state change.
    dlg._on_recovery_probe_done(False, False, None)
    assert dlg._game_recovery_pending
    assert not dlg._game_recovery_seen_down
    assert calls == []

    # Game client observed down: remember the transition edge, keep waiting.
    dlg._on_recovery_probe_done(False, False, False)
    assert dlg._game_recovery_pending
    assert dlg._game_recovery_seen_down
    assert calls == []

    # Client came up (EGM finished booting): ALL meters refetch.
    dlg._on_recovery_probe_done(False, False, True)
    assert not dlg._game_recovery_pending
    assert ("compare", {"prefetch": True, "force": True}) in calls
    assert ("fetch", {"prefetch": True, "force": True}) in calls

    # Successful COM capture clears a pending game watch too.
    dlg._game_recovery_pending = True
    dlg._apply_meter_fetch_complete(None)
    assert not dlg._game_recovery_pending

    _close_dialog(app, dlg)


def test_dialog_game_recovery_client_already_up_stops_instead_of_looping() -> None:
    """Dead link with a running client is a cable problem — never loop captures."""
    from gui.sas_verify_dialog import game_link_dead_status

    assert "SAS link is silent" in game_link_dead_status("OneHand.exe", "10.0.0.90")

    app, dlg = _make_dialog()
    calls: list[tuple] = []
    dlg._begin_cabinet_compare = lambda **kw: calls.append(("compare", kw))
    dlg._begin_meter_fetch = lambda **kw: calls.append(("fetch", kw))
    _set_scan_root_silently(dlg, r"\\127.0.0.1\c$\Goldclub\var")

    dlg._arm_game_recovery()
    assert dlg._game_recovery_pending

    # First probe already sees the client running: no down->up transition, so
    # waiting cannot help — disarm with an honest cable/COM message.
    dlg._on_recovery_probe_done(False, False, True)
    assert not dlg._game_recovery_pending
    assert calls == []
    assert "SAS link is silent" in dlg._prefetch_status_label.text()

    _close_dialog(app, dlg)


def test_resolve_scan_root_never_remaps_to_g_drive(monkeypatch) -> None:
    """A UNC hint with a share hiccup must not silently swap the root to G:\\."""
    import network.goldclub_paths as gp

    unc = r"\\10.0.0.90\c$\Goldclub\var\log"
    monkeypatch.setattr(
        gp,
        "resolve_log_scan_root",
        lambda hint, remote_ip=None, exe_dir=None: gp.StartupScanDiscovery(
            mode="remote", scan_root=unc, game_kind="slot", remote_ip="10.0.0.90"
        ),
    )
    # Share hiccup: the UNC has no reachable meter state, but G:\ does.
    monkeypatch.setattr(
        gp,
        "_layout_has_meter_state",
        lambda root, require_files=True: gp.is_game_image_drive_path(root),
    )
    monkeypatch.setattr(gp, "_path_exists_dir", lambda p: True)
    monkeypatch.setattr(
        gp,
        "_sas_verify_fallback_roots",
        lambda d: [unc, r"G:\var\log\ruleta", r"G:\var\log"],
    )

    resolved = gp.resolve_sas_verify_scan_root(unc, remote_ip="10.0.0.90")
    assert resolved.scan_root == unc  # stays on the cabinet share

    # An explicit G:\ hint may still remap within the G:\ drive.
    g_hint = r"G:\var\log\ruleta"
    monkeypatch.setattr(
        gp,
        "resolve_log_scan_root",
        lambda hint, remote_ip=None, exe_dir=None: gp.StartupScanDiscovery(
            mode="local", scan_root=g_hint, game_kind="roulette", remote_ip=None
        ),
    )
    monkeypatch.setattr(
        gp,
        "_layout_has_meter_state",
        lambda root, require_files=True: root == r"G:\var\log",
    )
    monkeypatch.setattr(
        gp,
        "_sas_verify_fallback_roots",
        lambda d: [g_hint, r"G:\var\log"],
    )
    resolved = gp.resolve_sas_verify_scan_root(g_hint)
    assert resolved.scan_root == r"G:\var\log"


def test_dialog_link_dead_error_arms_game_recovery() -> None:
    app, dlg = _make_dialog()
    calls: list[tuple] = []
    dlg._begin_cabinet_compare = lambda **kw: calls.append(("compare", kw))
    dlg._begin_meter_fetch = lambda **kw: calls.append(("fetch", kw))
    dlg._prompt_local_d_scan_root = lambda **kw: calls.append(("prompt", kw))
    _set_scan_root_silently(dlg, r"\\127.0.0.1\c$\Goldclub\var")

    dlg._meter_fetch_prefetch = True
    dlg._meter_prefetch_retried = True  # full-timing retry already spent
    dlg._on_meter_fetch_error("SAS link not responding on COM4 (no RX during 2.0s sync)")
    assert dlg._game_recovery_pending

    _close_dialog(app, dlg)


def test_dialog_has_help_menu_with_setup_dialog() -> None:
    app, dlg = _make_dialog()
    bar = dlg._build_view_menu_bar()
    titles = [a.text().replace("&", "") for a in bar.actions()]
    assert "Help" in titles

    dlg._show_help_dialog()
    help_dlg = dlg._help_dialog
    assert help_dlg.isVisible()
    assert "Help" in help_dlg.windowTitle()
    help_dlg.hide()

    _close_dialog(app, dlg)


def test_com_blocked_dialog_has_share_fetch_button() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    box = build_com_blocked_cabinet_share_dialog(None, body="test body")
    labels = [b.text() for b in box.buttons()]
    assert CABINET_SHARE_FETCH_BUTTON_LABEL in labels
    assert CABINET_SHARE_WAIT_COM_BUTTON_LABEL in labels
    assert "SASControler1" in CABINET_SHARE_FETCH_BUTTON_LABEL
    box.close()


def test_should_offer_cabinet_share_on_access_denied_without_named_blocker() -> None:
    assert should_offer_cabinet_share_when_com_blocked(
        has_remote_unc=True,
        port_busy=True,
    )


def test_game_recovery_disarms_after_inconclusive_probes() -> None:
    from gui.sas_verify_dialog import GAME_RECOVERY_INCONCLUSIVE_MAX

    app, dlg = _make_dialog()
    dlg._scan_root = r"\\10.0.0.90\c$\Goldclub\var"
    dlg._arm_game_recovery()
    assert dlg._game_recovery_pending
    for _ in range(GAME_RECOVERY_INCONCLUSIVE_MAX - 1):
        dlg._on_recovery_probe_done(False, False, None)
        assert dlg._game_recovery_pending
    dlg._on_recovery_probe_done(False, False, None)
    assert not dlg._game_recovery_pending
    assert "WinRM unreachable" in dlg._prefetch_status_label.text()
    _close_dialog(app, dlg)


def test_share_recovery_reloads_when_compare_stuck() -> None:
    import time

    app, dlg = _make_dialog()
    calls: list[dict] = []
    dlg._scan_root = r"\\127.0.0.1\c$\Goldclub\var"
    dlg._share_recovery_pending = True
    dlg._compare_running = lambda: True  # type: ignore[method-assign]
    dlg._compare_started_mono = time.monotonic() - 25.0
    dlg._begin_cabinet_compare = lambda **kw: calls.append(kw)
    dlg._stop_compare_thread = lambda **kw: None  # type: ignore[method-assign]
    dlg._invalidate_machine_cabinet_cache = lambda **kw: None  # type: ignore[method-assign]

    dlg._on_recovery_probe_done(False, True)
    assert not dlg._share_recovery_pending
    assert dlg._share_recovery_reloading
    assert calls == [{"prefetch": True, "force": True}]

    _close_dialog(app, dlg)


def test_recovery_timer_probes_share_while_compare_running(monkeypatch) -> None:
    app, dlg = _make_dialog()
    dlg._share_recovery_pending = True
    dlg._compare_running = lambda: True  # type: ignore[method-assign]
    captured: list[bool] = []

    class _FakeTask:
        def __init__(self, **kwargs) -> None:
            captured.append(bool(kwargs.get("check_share")))

    class _FakePool:
        def start(self, task) -> None:
            pass

    monkeypatch.setattr("gui.sas_verify_dialog._RecoveryProbeTask", _FakeTask)
    dlg._pool = _FakePool()  # type: ignore[assignment]
    dlg._resolve_active_scan_root = lambda: r"\\127.0.0.1\c$\Goldclub\var"  # type: ignore[method-assign]

    dlg._on_recovery_timer()
    assert captured == [True]

    _close_dialog(app, dlg)


def test_recovery_recapture_pairs_with_machine_when_auto_fetch_off() -> None:
    app, dlg = _make_dialog()
    dlg._auto_fetch_toggle.setChecked(False)
    dlg._auto_fetch_timer.stop()
    dlg._scan_root = r"\\10.0.0.90\c$\Goldclub\var\log"
    resyncs: list[bool] = []
    dlg._resync_machine_after_auto_fetch_capture = (  # type: ignore[method-assign]
        lambda: resyncs.append(True)
    )
    dlg._apply_meter_fetch_result = lambda r: False  # type: ignore[method-assign]
    dlg._flush_pending_cabinet_ui_refresh = lambda: None  # type: ignore[method-assign]
    dlg._machine_loaded_for_current_root = lambda: True  # type: ignore[method-assign]
    dlg._update_prefetch_status = lambda *a, **k: None  # type: ignore[method-assign]

    dlg._recovery_recapture_pending = True
    dlg._auto_fetch_round_active = False
    dlg._apply_meter_fetch_complete(None)
    assert resyncs == [True]
    assert dlg._auto_fetch_round_active is True
    assert dlg._recovery_recapture_pending is False

    _close_dialog(app, dlg)


def test_stale_meter_fetch_orphan_releases_ui(monkeypatch) -> None:
    import time

    from gui.sas_verify_dialog import METER_FETCH_STALE_S

    app, dlg = _make_dialog()
    dlg._meter_fetch_started_mono = time.monotonic() - METER_FETCH_STALE_S - 5.0
    dlg._meter_fetch_job_id = 3
    dlg._active_meter_fetch_job_id = 3
    dlg._meters_ui_pending = True
    dlg._manual_meters_refresh = True
    stopped: list[bool] = []
    dlg._meter_fetch_running = lambda: True  # type: ignore[method-assign]
    dlg._stop_meter_fetch_thread = lambda **kw: stopped.append(True) or True  # type: ignore[method-assign]
    dlg._set_busy_progress_active = lambda: None  # type: ignore[method-assign]
    dlg._update_prefetch_status = lambda *a, **k: None  # type: ignore[method-assign]

    dlg._orphan_stale_meter_fetch_if_needed()
    assert stopped == [True]
    assert dlg._active_meter_fetch_job_id == 4
    assert not dlg._meters_ui_pending
    assert not dlg._manual_meters_refresh

    _close_dialog(app, dlg)


def test_recovery_probe_done_always_emits_after_share_exception(monkeypatch) -> None:
    """Share-branch Exception must still release the probe latch via done.emit."""
    from gui.sas_verify_dialog import _RecoveryProbeSignals, _RecoveryProbeTask

    app, dlg = _make_dialog()
    seen: list[tuple] = []

    class _Sig(_RecoveryProbeSignals):
        def __init__(self) -> None:
            super().__init__()
            self.done.connect(lambda *a: seen.append(a))

    sig = _Sig()

    def _boom(*_a, **_k):
        raise RuntimeError("smb blew up")

    monkeypatch.setattr(
        "network.goldclub_paths.unc_share_scan_root_reachable", _boom
    )
    monkeypatch.setattr(
        "network.lab_access.ensure_lab_smb_credential", lambda *_a, **_k: None
    )
    task = _RecoveryProbeTask(
        port="",
        scan_root=r"\\10.0.0.90\c$\Goldclub\var",
        check_com=False,
        check_share=True,
        signals=sig,
    )
    task.run()
    assert seen == [(False, False, None)]

    _close_dialog(app, dlg)


def test_recovery_timer_clears_stale_probe_latch() -> None:
    import time

    app, dlg = _make_dialog()
    dlg._share_recovery_pending = True
    dlg._recovery_probe_running = True
    dlg._recovery_probe_started_mono = time.monotonic() - 61.0
    started: list[bool] = []

    class _FakePool:
        def start(self, task) -> None:
            started.append(True)

    dlg._pool = _FakePool()  # type: ignore[assignment]
    dlg._resolve_active_scan_root = lambda: r"\\127.0.0.1\c$\Goldclub\var"  # type: ignore[method-assign]
    dlg._on_recovery_timer()
    assert started == [True]
    assert dlg._recovery_probe_running is True
    assert dlg._recovery_probe_started_mono > 0.0

    _close_dialog(app, dlg)
