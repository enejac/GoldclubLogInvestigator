"""Unit tests for soak watchdog helpers (no live cabinet)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from automation.soak_watchdog import (
    SI,
    heartbeat_age_sec,
    parse_until,
    run_once,
    write_active_path,
)


def test_parse_until_naive_is_slovenia():
    dt = parse_until("2026-08-17T07:30:00")
    assert dt.astimezone(SI).hour == 7


def test_heartbeat_age_recent():
    now = datetime.now(timezone.utc)
    hb = {"heartbeat_si": (now - timedelta(minutes=10)).astimezone(SI).isoformat()}
    age = heartbeat_age_sec(hb)
    assert age is not None
    assert 500 < age < 900


def test_run_once_ok_when_alive(tmp_path: Path, monkeypatch):
    import automation.soak_watchdog as wd

    monkeypatch.setattr(wd, "ROOT", tmp_path)
    monkeypatch.setattr(wd, "ACTIVE_PTR", tmp_path / "active.txt")
    out = tmp_path / "automation_runs" / "x_10.0.0.90_two_week_soak"
    out.mkdir(parents=True)
    hb_time = datetime.now(SI).isoformat(timespec="seconds")
    (out / "heartbeat.json").write_text(
        '{"cycle": 3, "kind": "slot", "alive": true, "heartbeat_si": "%s"}' % hb_time,
        encoding="utf-8",
    )
    write_active_path(out)

    with (
        patch.object(wd, "free_bytes_on_drive", return_value=5 * 1024**3),
        patch.object(wd, "python_soak_alive", return_value=True),
        patch.object(wd, "any_python_soak", return_value=True),
        patch.object(wd, "topup_credits") as topup,
        patch(
            "automation.remote_input_agent.prune_stale_input_agent_bins",
            return_value={"removed": 0, "kept": 1, "errors": 0},
        ),
        patch(
            "automation.remote_input_agent.input_agent_source_sha12",
            return_value="a66e7e5eeb10",
        ),
        patch(
            "automation.egm_credit_inject.detect_live_egm_kind",
            return_value="slot",
        ),
        patch(
            "automation.egm_credit_inject.read_credits",
            return_value=type("R", (), {"credits": 200_000, "source": "x", "kind": "slot"})(),
        ),
    ):
        report = run_once(ip="10.0.0.90", heal=True)
    assert report.ok is True
    assert report.python_alive is True
    assert report.cycle == 3
    topup.assert_not_called()


def test_run_once_restarts_when_python_dead(tmp_path: Path, monkeypatch):
    import automation.soak_watchdog as wd

    monkeypatch.setattr(wd, "ROOT", tmp_path)
    monkeypatch.setattr(wd, "ACTIVE_PTR", tmp_path / "active.txt")
    (tmp_path / "automation_runs").mkdir()
    out = tmp_path / "automation_runs" / "old_10.0.0.90_two_week_soak"
    out.mkdir()
    old_hb = (datetime.now(SI) - timedelta(hours=3)).isoformat(timespec="seconds")
    (out / "heartbeat.json").write_text(
        '{"cycle": 1, "kind": "slot", "alive": true, "heartbeat_si": "%s"}' % old_hb,
        encoding="utf-8",
    )
    write_active_path(out)
    started: list[Path] = []

    def fake_start(ip, until, out_root):
        started.append(out_root)
        out_root.mkdir(parents=True, exist_ok=True)
        write_active_path(out_root)

    with (
        patch.object(wd, "free_bytes_on_drive", return_value=5 * 1024**3),
        patch.object(wd, "python_soak_alive", return_value=False),
        patch.object(wd, "any_python_soak", return_value=False),
        patch.object(wd, "stop_soak"),
        patch.object(wd, "start_supervisor", side_effect=fake_start),
        patch(
            "automation.remote_input_agent.prune_stale_input_agent_bins",
            return_value={"removed": 2, "kept": 1, "errors": 0},
        ),
        patch(
            "automation.remote_input_agent.input_agent_source_sha12",
            return_value="a66e7e5eeb10",
        ),
        patch(
            "automation.egm_credit_inject.detect_live_egm_kind",
            return_value="slot",
        ),
        patch(
            "automation.egm_credit_inject.read_credits",
            return_value=type("R", (), {"credits": 200_000, "source": "x", "kind": "slot"})(),
        ),
    ):
        report = run_once(ip="10.0.0.90", heal=True, until="2026-08-17T07:30:00")
    assert started == [out]
    assert any("restarted_supervisor" in a for a in report.actions)
    assert any("pruned_remote_inputagent" in a for a in report.actions)
