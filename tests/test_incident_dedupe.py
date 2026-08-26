"""ViewModel deduplication of mirrored log lines across files."""

from datetime import datetime, timezone

from parser import Incident
from gui.view_model import IncidentViewModel, _logical_incident_key, collapse_consecutive_incidents_to_rows


def _sample(path: str, line_no: int) -> Incident:
    return Incident(
        timestamp=datetime(2026, 3, 24, 7, 47, 12, 68000, tzinfo=timezone.utc),
        game="PR3_BigWinHD",
        severity="MEDIUM",
        error_type="Assets / Dispose / Resources",
        probable_cause="x",
        log_file_path=path,
        line_number=line_no,
        line_snippet=(
            "WARN [SlotMachine] OneHand.Utilities.RhEffect - "
            "RhEffect::Dispose called without disposing Effect object."
        ),
    )


def test_logical_key_stable() -> None:
    a = _sample(r"C:\a\SlotLog\day.log", 118852)
    b = _sample(r"C:\b\OneHand\day.log", 99)
    assert _logical_incident_key(a) == _logical_incident_key(b)


def test_append_live_dedupes_mirrored_lines() -> None:
    vm = IncidentViewModel()
    batch = [
        _sample(r"C:\SlotLog\2026-03-24.log", 118852),
        _sample(r"C:\OneHand\2026-03-24.log", 12),
        _sample(r"C:\OneHand GAME EVENTS\2026-03-24.log", 5),
    ]
    vm.append_live_incidents(batch)
    assert len(vm.all_incidents) == 1


def test_distinct_snippet_not_deduped() -> None:
    vm = IncidentViewModel()
    a = _sample("a.log", 1)
    b = Incident(
        timestamp=a.timestamp,
        game=a.game,
        severity=a.severity,
        error_type=a.error_type,
        probable_cause=a.probable_cause,
        log_file_path="b.log",
        line_number=2,
        line_snippet=a.line_snippet + " extra",
    )
    vm.append_live_incidents([a, b])
    assert len(vm.all_incidents) == 2


def test_collapse_groups_same_signature_different_timestamps() -> None:
    a = _sample("a.log", 1)
    b = Incident(
        timestamp=datetime(2026, 3, 24, 8, 0, 0, tzinfo=timezone.utc),
        game=a.game,
        severity=a.severity,
        error_type=a.error_type,
        probable_cause=a.probable_cause,
        log_file_path="a.log",
        line_number=2,
        line_snippet=(
            "2026-03-24T08:00:00.000+00:00 WARN [SlotMachine] OneHand.Utilities.RhEffect - "
            "RhEffect::Dispose called without disposing Effect object."
        ),
    )
    rows = collapse_consecutive_incidents_to_rows([a, b], collapse=True)
    assert len(rows) == 1
    assert rows[0].count == 2


def test_live_append_announces_insert_before_rows_appear() -> None:
    """Qt requires rowCount() to still report the old total inside beginInsertRows."""
    def _distinct(n: int) -> Incident:
        base = _sample(f"{n}.log", n)
        return Incident(
            timestamp=datetime(2026, 3, 24, 7, 47, n, tzinfo=timezone.utc),
            game=base.game,
            severity=base.severity,
            error_type=base.error_type,
            probable_cause=base.probable_cause,
            log_file_path=base.log_file_path,
            line_number=n,
            line_snippet=f"{base.line_snippet} #{n}",
        )

    vm = IncidentViewModel()
    vm.set_collapse_duplicates(False)
    vm.append_live_incidents([_distinct(1)])

    seen: list[tuple[str, int, int, int]] = []
    vm.rows_about_to_be_inserted.connect(
        lambda first, last: seen.append(("about", first, last, vm.filtered_row_count()))
    )
    vm.rows_inserted.connect(
        lambda first, last: seen.append(("done", first, last, vm.filtered_row_count()))
    )

    vm.append_live_incidents([_distinct(2), _distinct(3)])

    assert [entry[0] for entry in seen] == ["about", "done"]
    about, done = seen
    assert about[1] == 1 and about[2] == 2
    assert about[3] == 1  # row count unchanged while the insert is announced
    assert done[1:3] == about[1:3]
    assert done[3] == 3  # rows are present by the time the insert completes
