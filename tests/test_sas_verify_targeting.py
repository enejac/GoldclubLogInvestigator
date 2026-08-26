"""SAS Verify: which machine the COM capture is aimed at, and meter-derived counters."""

from __future__ import annotations

from gui.sas_verify_dialog import (
    SasVerifyDialog,
    _extract_unc_host,
    _unc_host_only,
    compute_game_summary,
)
from network import goldclub_paths, health_monitor


class _Targeting:
    """Just the scan-root plumbing of the dialog, without building a QDialog."""

    _scan_root_text = SasVerifyDialog._scan_root_text
    _scan_root_unc_host = SasVerifyDialog._scan_root_unc_host
    _onehand_check_uses_local = SasVerifyDialog._onehand_check_uses_local

    def __init__(self, scan_root: str) -> None:
        self._scan_root = scan_root


def test_unc_host_only_reads_hostnames_and_ignores_local_paths() -> None:
    assert _unc_host_only(r"\\10.0.0.90\c$\Goldclub\var\log") == "10.0.0.90"
    assert _unc_host_only(r"\\GST20664\c$\Goldclub\var\log") == "GST20664"
    assert _unc_host_only(r"//10.0.0.83/c$/Goldclub") == "10.0.0.83"
    assert _unc_host_only(r"\\?\UNC\GST19737\c$\Goldclub") == "GST19737"
    assert _unc_host_only(r"C:\Goldclub\var\log") == ""
    # An address inside a *local* folder name is not a remote host.
    assert _unc_host_only(r"D:\exports\10.0.0.90\var\log") == ""
    assert _extract_unc_host(r"D:\exports\10.0.0.90\var\log") == "10.0.0.90"


def test_remote_unc_scan_root_is_not_treated_as_this_cabinet(monkeypatch) -> None:
    """Wrong answer here applies on-cabinet MUX wire order to a host COM cable."""
    monkeypatch.setattr(health_monitor, "is_this_host", lambda host: False)
    monkeypatch.setattr(health_monitor, "is_running_on_local_egm", lambda: False)
    # Fleet IP, hostname, and an address outside the lab allowlist are all remote.
    assert not _Targeting(r"\\10.0.0.90\c$\Goldclub\var\log")._onehand_check_uses_local()
    assert not _Targeting(r"\\GST20664\c$\Goldclub\var\log")._onehand_check_uses_local()
    assert not _Targeting(r"\\192.0.2.5\c$\Goldclub\var\log")._onehand_check_uses_local()


def test_unc_scan_root_pointing_at_this_machine_is_local(monkeypatch) -> None:
    monkeypatch.setattr(health_monitor, "is_this_host", lambda host: host == "GST20664")
    monkeypatch.setattr(health_monitor, "is_running_on_local_egm", lambda: False)
    assert _Targeting(r"\\GST20664\c$\Goldclub\var\log")._onehand_check_uses_local()


def test_usb_export_scan_root_has_no_local_sas_link(monkeypatch) -> None:
    monkeypatch.setattr(health_monitor, "is_this_host", lambda host: False)
    monkeypatch.setattr(health_monitor, "is_running_on_local_egm", lambda: False)
    monkeypatch.setattr(
        goldclub_paths,
        "resolve_goldclub_layout",
        lambda _root: goldclub_paths.GoldclubLayout(
            scan_root=goldclub_paths.Path(r"E:\log_01_02_2026"),
            log_root=goldclub_paths.Path(r"E:\log_01_02_2026"),
            state_gcmessenger=None,
            themes_root=None,
            goldclub_root=None,
            kind=goldclub_paths.GoldclubLayoutKind.USB_EXPORT,
        ),
    )
    assert not _Targeting(r"E:\log_01_02_2026")._onehand_check_uses_local()


def test_on_cabinet_install_is_local(monkeypatch) -> None:
    monkeypatch.setattr(health_monitor, "is_running_on_local_egm", lambda: True)
    assert _Targeting(r"C:\Goldclub\var\log")._onehand_check_uses_local()


def test_compute_game_summary_tolerates_non_numeric_meters() -> None:
    """One unreadable meter must not abort the whole Game tab."""
    summary = compute_game_summary(
        played_raw="N/A",
        won_raw="—",
        lost_raw="",
        bet_raw="0",
        win_raw="0",
    )
    assert summary["played"] == 0
    assert summary["won"] == 0
    assert summary["lost"] == 0
    assert summary["yield_pct"] is None

    real = compute_game_summary(
        played_raw="120",
        won_raw="45",
        lost_raw="",
        bet_raw="10000",
        win_raw="9000",
    )
    assert real["played"] == 120
    assert real["lost"] == 75
    assert real["bet_minus_win"] == real["bet"] - real["win"]
