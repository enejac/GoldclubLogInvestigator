"""Seamless trial transfer: log state, secrets, prep gates."""

from __future__ import annotations

from pathlib import Path

from config_scanner.cabinet_secrets import load_cabinet_secrets, resolve_trial_password
from config_scanner.cabinet_trial_prep import (
    DEFAULT_LAB_CLOCK,
    needs_seamless_trial_prep,
    prepare_cabinet_for_seamless_transfer,
)
from roulette_trial import trial_log_state


def test_trial_log_state_displayed_wins_over_old_succeeded(tmp_path: Path) -> None:
    log_dir = tmp_path / "var" / "log" / "ruleta Roulette"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "2026-08-24.log"
    log_path.write_text(
        '<TRIAL error="99" type="SUCCEEDED">123</TRIAL>\n'
        '<TRIAL error="99" type="DISPLAYED">45678901234567890123</TRIAL>\n',
        encoding="utf-8",
    )
    assert trial_log_state(tmp_path) == "displayed"


def test_trial_log_state_accepted_when_newest(tmp_path: Path) -> None:
    log_dir = tmp_path / "var" / "log" / "ruleta Roulette"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "2026-08-24.log"
    log_path.write_text(
        '<TRIAL error="99" type="DISPLAYED">45678901234567890123</TRIAL>\n'
        '<TRIAL error="99" type="SUCCEEDED">123</TRIAL>\n',
        encoding="utf-8",
    )
    assert trial_log_state(tmp_path) == "accepted"


def test_trial_log_state_re_locked(tmp_path: Path) -> None:
    log_dir = tmp_path / "var" / "log" / "ruleta Roulette"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "2026-08-24.log"
    log_path.write_text(
        '<TRIAL error="99" type="SUCCEEDED">123</TRIAL>\n'
        "Trial expired\n",
        encoding="utf-8",
    )
    assert trial_log_state(tmp_path) == "re-locked"


def test_resolve_trial_password_env(monkeypatch) -> None:
    monkeypatch.setenv("RULETA_ERROR30_PASSWORD", "from-env")
    assert resolve_trial_password(host="10.0.0.111") == "from-env"


def test_load_cabinet_secrets_skips_underscore_keys(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "config-scanner"
    secrets_dir.mkdir()
    path = secrets_dir / "cabinet_secrets.json"
    path.write_text(
        '{"_comment": "x", "10.0.0.111": "secret123"}',
        encoding="utf-8",
    )
    secrets = load_cabinet_secrets(tmp_path)
    assert secrets == {"10.0.0.111": "secret123"}


def test_lab_clock_has_no_colons_for_elevate_cmdline() -> None:
    """GoldClubElevate joins args unquoted; a time-of-day would split -TargetLocal."""
    assert ":" not in DEFAULT_LAB_CLOCK
    assert DEFAULT_LAB_CLOCK.startswith("2026-")


def test_prepare_can_skip_trial_password_sync() -> None:
    import inspect

    src = inspect.getsource(prepare_cabinet_for_seamless_transfer)
    assert "sync_password: bool = True" in src
    assert "skipped trial password sync" in src
    assert "clear_auto_llave_password_files" in src


def test_needs_seamless_trial_prep(monkeypatch) -> None:
    from config_scanner.stack_restart import StackRestartPlan

    monkeypatch.setattr(
        "config_scanner.cabinet_trial_prep.plan_stack_restart",
        lambda _t: StackRestartPlan(
            mode="remote",
            host="10.0.0.111",
            kill_ps1="k",
            run_ps1="r",
        ),
    )
    assert needs_seamless_trial_prep(
        r"\\10.0.0.111\slot",
        write_scope="full_software",
        version_transfer=True,
    )
    assert not needs_seamless_trial_prep(
        r"\\10.0.0.111\slot",
        write_scope="full",
        version_transfer=True,
    )
    assert not needs_seamless_trial_prep(
        r"\\10.0.0.111\slot",
        write_scope="full_software",
        version_transfer=False,
    )


def test_run_remote_one_accepts_fix_error30_clock_log_success(monkeypatch) -> None:
    from config_scanner.stack_restart import _run_remote_one

    class _Result:
        returncode = 1
        stdout = "[2026-08-10T12:00:00] Set-Date now=8/10/2026\nSkipLaunch"
        stderr = "Parameter set cannot be resolved using the specified named parameters."

    monkeypatch.setattr(
        "network.lab_access.ensure_lab_smb_credential",
        lambda _h: None,
    )
    monkeypatch.setattr(
        "network.lab_access.require_lab_fleet_ip",
        lambda h: h,
    )
    monkeypatch.setattr(
        "automation.remote_exec.winrm_run_elevated_script",
        lambda **_kw: _Result(),
    )
    ok, detail = _run_remote_one(
        "10.0.0.111",
        r"D:\usb_scripts\roulette\Fix-Error30Clock.ps1",
        label="Fix-Error30Clock",
        allow_exit_1=False,
        timeout=60,
        script_args=["-SkipKill", "-SkipLaunch", "-TargetLocal", "2026-08-10T12:00:00"],
    )
    assert ok
    assert "clock + trial wipe" in detail
