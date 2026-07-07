"""Quick filter chip logic (additive OR)."""

from parser import Incident
from gui.filter_runnable import QuickFilterSnapshot, quick_filter_match


def _inc(**kwargs: object) -> Incident:
    base = dict(
        timestamp=None,
        game="g",
        severity="LOW",
        error_type="x",
        probable_cause="",
        log_file_path="p",
        line_number=1,
        line_snippet="",
    )
    base.update(kwargs)
    return Incident(**base)  # type: ignore[arg-type]


def test_no_chips_passes_all() -> None:
    q = QuickFilterSnapshot()
    assert quick_filter_match(_inc(severity="LOW"), q)
    assert quick_filter_match(_inc(severity="CRITICAL"), q)


def test_critical_chip() -> None:
    q = QuickFilterSnapshot(critical=True)
    assert quick_filter_match(_inc(severity="CRITICAL"), q)
    assert not quick_filter_match(_inc(severity="MEDIUM"), q)
    assert quick_filter_match(_inc(severity="LOW", line_snippet="something FATAL x"), q)


def test_warn_chip() -> None:
    q = QuickFilterSnapshot(warn=True)
    assert quick_filter_match(_inc(severity="MEDIUM"), q)
    assert quick_filter_match(_inc(severity="LOW", line_snippet="WARN here"), q)
    assert not quick_filter_match(_inc(severity="CRITICAL"), q)


def test_additive_or() -> None:
    q = QuickFilterSnapshot(critical=True, warn=True)
    assert quick_filter_match(_inc(severity="CRITICAL"), q)
    assert quick_filter_match(_inc(severity="MEDIUM"), q)
    assert not quick_filter_match(_inc(severity="LOW"), q)


def test_math_and_drift() -> None:
    qm = QuickFilterSnapshot(math_fails=True)
    assert quick_filter_match(
        _inc(severity="LOW", validation_status="FAIL"), qm
    )
    assert quick_filter_match(
        _inc(severity="LOW", error_type="CRITICAL MATH DISCREPANCY"), qm
    )
    assert not quick_filter_match(_inc(severity="LOW", validation_status="PASS"), qm)

    qd = QuickFilterSnapshot(drift=True)
    assert quick_filter_match(
        _inc(severity="LOW", error_type="SYSTEM DRIFT DETECTED"), qd
    )
    assert not quick_filter_match(_inc(severity="LOW", error_type="other"), qd)


def test_known_issues_chip_aft_pattern() -> None:
    q = QuickFilterSnapshot(known_issues=True)
    line = "Withdraw successful GCC_ST_20664_01 in 100000(cashable:0); 0(cashable:0)"
    assert quick_filter_match(_inc(severity="INFO", line_snippet=line), q)
    assert not quick_filter_match(_inc(severity="INFO", line_snippet="benign line"), q)
