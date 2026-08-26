"""Reporter signature and collapse grouping helpers."""

from datetime import datetime, timezone

from parser import Incident
from reporter import _normalize_signature, incident_group_key


def _inc(snippet: str, *, error_type: str = "Godot Unhandled Exception") -> Incident:
    return Incident(
        timestamp=datetime(2025, 11, 24, 14, 1, 14, tzinfo=timezone.utc),
        game="Godot UI",
        severity="CRITICAL",
        error_type=error_type,
        probable_cause="x",
        log_file_path=r"C:\log\godot1\2025-11-24.txt",
        line_number=1,
        line_snippet=snippet,
    )


def test_normalize_signature_strips_godot_space_timestamp() -> None:
    inc = _inc(
        "2025-11-24 14:01:14.9999 ERROR Unhandled exception: System.NullReferenceException: boom"
    )
    sig = _normalize_signature(inc)
    assert "2025-11-24" not in sig
    assert "NullReferenceException" in sig
    assert sig.startswith("Godot Unhandled Exception:")


def test_incident_group_key_ignores_timestamp_differences() -> None:
    a = _inc(
        "2025-11-24 14:01:14.9999 ERROR Unhandled exception: System.NullReferenceException: boom"
    )
    b = _inc(
        "2025-11-24 14:02:00.0000 ERROR Unhandled exception: System.NullReferenceException: boom"
    )
    assert incident_group_key(a) == incident_group_key(b)
