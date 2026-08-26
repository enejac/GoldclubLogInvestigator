"""Launch-time local EGM detection and remote-ops gating."""

from __future__ import annotations

from pathlib import Path

from network import app_runtime
from network.app_runtime import AppRuntimeContext


def test_wants_remote_only_for_other_host(monkeypatch) -> None:
    monkeypatch.setattr(app_runtime, "is_this_host", lambda ip: ip == "10.0.0.90")
    assert not app_runtime.wants_remote_operations(
        ui_remote=True, target_ip="10.0.0.90"
    )
    assert app_runtime.wants_remote_operations(
        ui_remote=True, target_ip="10.0.0.171"
    )
    assert not app_runtime.wants_remote_operations(
        ui_remote=False, target_ip="10.0.0.171"
    )
    assert (
        app_runtime.effective_remote_ip(ui_remote=True, target_ip="10.0.0.171")
        == "10.0.0.171"
    )
    assert (
        app_runtime.effective_remote_ip(ui_remote=True, target_ip="10.0.0.90")
        is None
    )


def test_prefer_local_on_cabinet_when_saved_remote_is_self(monkeypatch) -> None:
    monkeypatch.setattr(
        app_runtime,
        "get_app_runtime",
        lambda: AppRuntimeContext(
            on_local_egm=True,
            game_kind="roulette",
            local_ips=frozenset({"10.0.0.90"}),
            local_scan_root=r"C:\Goldclub\var\log\ruleta",
            install_root=r"C:\Goldclub",
        ),
    )
    monkeypatch.setattr(app_runtime, "is_this_host", lambda ip: ip == "10.0.0.90")
    assert app_runtime.prefer_local_connection_at_launch(
        saved_mode="remote",
        saved_remote_ip="10.0.0.90",
    )
    assert not app_runtime.prefer_local_connection_at_launch(
        saved_mode="remote",
        saved_remote_ip="10.0.0.171",
    )


def test_prefer_local_not_forced_on_workstation(monkeypatch) -> None:
    monkeypatch.setattr(
        app_runtime,
        "get_app_runtime",
        lambda: AppRuntimeContext(
            on_local_egm=False,
            game_kind="roulette",
            local_ips=frozenset({"10.0.0.165"}),
            local_scan_root=r"G:\var\log\ruleta",
            install_root="G:\\",
        ),
    )
    assert not app_runtime.prefer_local_connection_at_launch(
        saved_mode="remote",
        saved_remote_ip="10.0.0.90",
    )


def test_detect_ignores_g_drive_usb_as_on_egm(monkeypatch) -> None:
    monkeypatch.setattr(
        app_runtime, "local_ipv4_addresses", lambda: frozenset({"10.0.0.165"})
    )
    monkeypatch.setattr(app_runtime, "_has_local_game_binary", lambda: False)
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root",
        lambda: Path("G:/"),
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root",
        lambda: None,
    )
    monkeypatch.setattr(
        "network.goldclub_paths._log_root_for_roulette_install",
        lambda _r: Path(r"G:\var\log\ruleta"),
    )
    app_runtime.refresh_app_runtime()
    ctx = app_runtime.detect_app_runtime()
    assert ctx.on_local_egm is False
    assert ctx.game_kind == "roulette"
    assert ctx.local_scan_root == r"G:\var\log\ruleta"


def test_is_this_host_accepts_hostname_and_loopback(monkeypatch) -> None:
    monkeypatch.setattr(
        app_runtime, "local_ipv4_addresses", lambda: frozenset({"10.0.0.90"})
    )
    monkeypatch.setattr(
        app_runtime, "local_hostnames", lambda: frozenset({"gst20664"})
    )
    assert app_runtime.is_this_host("10.0.0.90")
    assert app_runtime.is_this_host("GST20664")
    assert app_runtime.is_this_host("gst20664")
    assert app_runtime.is_this_host("127.0.0.5")
    assert app_runtime.is_this_host("::1")
    assert app_runtime.is_this_host("[::1]")
    # A different cabinet, by IP or by name, is never this host.
    assert not app_runtime.is_this_host("10.0.0.171")
    assert not app_runtime.is_this_host("GST19737")
    assert not app_runtime.is_this_host("")


def test_detect_on_egm_via_c_goldclub(monkeypatch) -> None:
    monkeypatch.setattr(
        app_runtime, "local_ipv4_addresses", lambda: frozenset({"10.0.0.90"})
    )
    monkeypatch.setattr(app_runtime, "_has_local_game_binary", lambda: True)
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root",
        lambda: Path(r"C:\Goldclub"),
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root",
        lambda: None,
    )
    monkeypatch.setattr(
        "network.goldclub_paths._log_root_for_roulette_install",
        lambda _r: Path(r"C:\Goldclub\var\log\ruleta"),
    )
    ctx = app_runtime.detect_app_runtime()
    assert ctx.on_local_egm is True
    assert ctx.game_kind == "roulette"
