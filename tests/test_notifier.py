"""Live notification decision logic."""

from __future__ import annotations

from datetime import datetime, timezone

from parser import Incident
from network import notifier


def _inc(*, severity: str = "CRITICAL", line_snippet: str = "hello world") -> Incident:
    return Incident(
        timestamp=datetime(2000, 1, 1, tzinfo=timezone.utc),
        game="g",
        severity=severity,
        error_type="E",
        probable_cause="p",
        log_file_path="f",
        line_number=1,
        line_snippet=line_snippet,
    )


def test_should_notify_severity(monkeypatch) -> None:
    monkeypatch.setattr(notifier.SettingsManager, "get_notifications_enabled", lambda: True)
    monkeypatch.setattr(notifier.SettingsManager, "get_notification_blacklist", lambda: [])
    assert not notifier.should_notify(_inc(severity="LOW"))
    assert notifier.should_notify(_inc(severity="CRITICAL"))
    assert notifier.should_notify(_inc(severity="FATAL"))


def test_should_notify_blacklist(monkeypatch) -> None:
    monkeypatch.setattr(notifier.SettingsManager, "get_notifications_enabled", lambda: True)
    monkeypatch.setattr(notifier.SettingsManager, "get_notification_blacklist", lambda: ["noise"])
    assert not notifier.should_notify(_inc(line_snippet="has noise in line"))
    assert notifier.should_notify(_inc(line_snippet="clean"))


def test_should_notify_disabled(monkeypatch) -> None:
    monkeypatch.setattr(notifier.SettingsManager, "get_notifications_enabled", lambda: False)
    monkeypatch.setattr(notifier.SettingsManager, "get_notification_blacklist", lambda: [])
    assert not notifier.should_notify(_inc())


def test_incident_message_property() -> None:
    inc = _inc(line_snippet="  x  ")
    assert inc.message == "x"


def test_notification_ignore_fingerprint_truncates() -> None:
    long_snip = "a" * 500
    inc = _inc(line_snippet=long_snip)
    assert len(notifier.notification_ignore_fingerprint(inc)) == 400
