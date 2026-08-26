"""Tests for post-restore stack verification."""

from network.ruleta_stack_probe import verify_stack_running


def test_verify_stack_running_local_requires_ruleta(monkeypatch) -> None:
    monkeypatch.setattr(
        "network.ruleta_stack_probe.probe_blocking_processes",
        lambda _host: ("godot",),
    )
    ok, detail = verify_stack_running(None)
    assert ok is False
    assert "ruleta" in detail


def test_verify_stack_running_local_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        "network.ruleta_stack_probe.probe_blocking_processes",
        lambda _host: ("ruleta", "godot"),
    )
    ok, detail = verify_stack_running(None)
    assert ok is True
    assert "ruleta" in detail
