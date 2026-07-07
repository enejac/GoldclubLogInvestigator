"""Environment fingerprint extraction (LogDaemon Spawning lines)."""

from __future__ import annotations

from parser import _env_fingerprint_from_line


def test_spawning_app_only_real_shape() -> None:
    line = "2026-03-12T10:05:41.893+00:00 INFO [:30015890] Logging::Log() Spawning v1.2.1 ..."
    fp = _env_fingerprint_from_line(line)
    assert fp is not None
    assert fp.app_version == "1.2.1"
    assert fp.clr_version is None
    assert fp.os_version is None


def test_spawning_with_clr_os() -> None:
    line = (
        "INFO  Logging::Log() Spawning v2.0 , clr=4.0.30319 , os=Microsoft Windows NT 10.0.19045.0"
    )
    fp = _env_fingerprint_from_line(line)
    assert fp is not None
    assert fp.app_version == "2.0"
    assert fp.clr_version == "4.0.30319"
    assert "10.0.19045" in (fp.os_version or "")


def test_no_match() -> None:
    assert _env_fingerprint_from_line("INFO random text") is None
