from gui.sas_verify_app import _parse_args


def test_sas_verify_app_parse_defaults() -> None:
    args = _parse_args([])
    assert args.scan_root == ""
    assert args.ip == ""


def test_sas_verify_app_parse_flags() -> None:
    args = _parse_args(
        ["--scan-root", r"\\10.0.0.90\c$\Goldclub\var\log", "--ip", "10.0.0.90"]
    )
    assert "10.0.0.90" in args.scan_root
    assert args.ip == "10.0.0.90"


def test_default_startup_scan_root_is_var_not_var_log() -> None:
    """Scan root field default ends at …\\var — machine state lives there."""
    from gui.sas_verify_app import default_startup_scan_root

    root = default_startup_scan_root("10.0.0.90")
    assert root == r"\\10.0.0.90\c$\Goldclub\var"
    assert not root.lower().endswith("\\log")


def test_single_instance_bypass_with_env(monkeypatch) -> None:
    from gui.sas_verify_app import try_acquire_sas_verify_single_instance

    monkeypatch.setenv("SAS_VERIFY_ALLOW_MULTI", "1")
    assert try_acquire_sas_verify_single_instance() is not None


def test_single_instance_lock_roundtrip() -> None:
    """First acquire wins; second fails until the lock is released."""
    import os

    os.environ.pop("SAS_VERIFY_ALLOW_MULTI", None)
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_app import try_acquire_sas_verify_single_instance

    app = QApplication.instance() or QApplication([])
    first = try_acquire_sas_verify_single_instance()
    assert first is not None
    second = try_acquire_sas_verify_single_instance()
    assert second is None
    if hasattr(first, "detach"):
        first.detach()
    app._sas_verify_instance_lock = None  # type: ignore[attr-defined]
    third = try_acquire_sas_verify_single_instance()
    assert third is not None
    if hasattr(third, "detach"):
        third.detach()


def test_single_instance_reacquires_after_release() -> None:
    """After the holder releases (crash / CloseHandle), a new launch must succeed."""
    import os

    os.environ.pop("SAS_VERIFY_ALLOW_MULTI", None)
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_app import try_acquire_sas_verify_single_instance

    QApplication.instance() or QApplication([])
    lock = try_acquire_sas_verify_single_instance()
    assert lock is not None
    assert try_acquire_sas_verify_single_instance() is None
    lock.detach()
    again = try_acquire_sas_verify_single_instance()
    assert again is not None
    again.detach()


def test_prefer_local_cabinet_scan_root_on_egm_replaces_self_unc(monkeypatch) -> None:
    from network.app_runtime import AppRuntimeContext

    from gui.sas_verify_app import prefer_local_cabinet_scan_root_on_egm

    monkeypatch.setattr(
        "network.app_runtime.is_running_on_local_egm",
        lambda: True,
    )
    monkeypatch.setattr(
        "network.app_runtime.is_this_host",
        lambda host: host in {"10.0.0.90", "GST20664"},
    )
    monkeypatch.setattr(
        "network.app_runtime.get_app_runtime",
        lambda: AppRuntimeContext(
            on_local_egm=True,
            game_kind="slot",
            local_ips=frozenset({"10.0.0.90"}),
            local_scan_root=r"C:\Goldclub\var\log",
            install_root=r"C:\Goldclub",
        ),
    )
    unc = r"\\10.0.0.90\c$\Goldclub\var\log"
    assert prefer_local_cabinet_scan_root_on_egm(unc) == r"C:\Goldclub\var\log"
    assert (
        prefer_local_cabinet_scan_root_on_egm(r"\\GST20664\c$\Goldclub\var")
        == r"C:\Goldclub\var\log"
    )
    assert prefer_local_cabinet_scan_root_on_egm(unc, user_hint=True) == unc
    assert (
        prefer_local_cabinet_scan_root_on_egm(
            r"\\10.0.0.171\c$\Goldclub\var", remote_ip="10.0.0.171"
        )
        == r"\\10.0.0.171\c$\Goldclub\var"
    )


def test_resolve_initial_scan_root_on_egm_overrides_persisted_self_unc(monkeypatch) -> None:
    from network.app_runtime import AppRuntimeContext

    from gui.sas_verify_app import resolve_initial_scan_root

    monkeypatch.setattr(
        "gui.sas_verify_dialog.load_persisted_scan_root",
        lambda: r"\\10.0.0.90\c$\Goldclub\var\log",
    )
    monkeypatch.setattr(
        "network.app_runtime.is_running_on_local_egm",
        lambda: True,
    )
    monkeypatch.setattr(
        "network.app_runtime.is_this_host",
        lambda host: host == "10.0.0.90",
    )
    monkeypatch.setattr(
        "network.app_runtime.get_app_runtime",
        lambda: AppRuntimeContext(
            on_local_egm=True,
            game_kind="slot",
            local_ips=frozenset({"10.0.0.90"}),
            local_scan_root=r"C:\Goldclub\var\log",
            install_root=r"C:\Goldclub",
        ),
    )
    root = resolve_initial_scan_root(remote_ip=None, default_ip="10.0.0.90")
    assert root == r"C:\Goldclub\var\log"


def test_sas_verify_runtime_resolves_machine_meters() -> None:
    """Standalone exe must resolve Accounting Machine values (audit P0)."""
    from gui.sas_verify_app import SasVerifyRuntime

    rt = SasVerifyRuntime()
    assert rt.thread_pool() is not None
    state = {
        "watcashableinamt": "850000",
        "watnoncashinamt": "10000",
        "watpromoinamt": "2000",
        "notesinstackeramt": "1000",
    }
    assert rt.get_gm2u_value_for_sas_code("00A0", state) == "850000"
    assert rt.get_gm2u_value_for_sas_code("000B", state) == "1000"
    assert rt.SAS_6F_METER_ALIASES
    rt.invalidate_accounting_registers_cache()
    rt.invalidate_accounting_registers_cache(r"C:\Goldclub\var")


def test_sas_verify_runtime_init_does_not_import_view_model() -> None:
    import inspect

    from gui.sas_verify_app import SasVerifyRuntime

    src = inspect.getsource(SasVerifyRuntime.__init__)
    assert "view_model" not in src
    assert "IncidentViewModel" not in src


def test_launcher_shows_splash_and_paints_without_process_events() -> None:
    from pathlib import Path

    text = Path("gui/sas_verify_app.py").read_text(encoding="utf-8")
    assert "_show_opening_splash" in text
    assert "dlg.repaint()" in text
    assert "app.processEvents()" not in text
    assert "constructing dialog" in text


def test_standalone_launcher_uses_runtime_not_full_view_model_graph() -> None:
    from pathlib import Path

    text = Path("gui/sas_verify_app.py").read_text(encoding="utf-8")
    # Must not construct a live IncidentViewModel (heavy LI graph); bare
    # object.__new__ + meter methods on SasVerifyRuntime is the contract.
    assert "IncidentViewModel(" not in text
    assert "SasVerifyRuntime" in text
    assert "get_gm2u_value_for_sas_code" in text
