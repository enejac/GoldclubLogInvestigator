from gui.thin_progress import make_thin_busy_progress, set_thin_busy_progress_active


def test_thin_busy_progress_keeps_height_when_toggled() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    bar = make_thin_busy_progress()
    assert bar.height() == 3 or bar.maximumHeight() == 3
    assert bar.minimumHeight() == 3
    assert bar.maximum() == 1
    assert bar.value() == 0

    set_thin_busy_progress_active(bar, True)
    assert bar.minimum() == 0
    assert bar.maximum() == 0  # indeterminate

    set_thin_busy_progress_active(bar, False)
    assert bar.maximum() == 1
    assert bar.value() == 0
    assert bar.minimumHeight() == 3
    _ = app


def test_thin_busy_progress_idempotent_while_busy() -> None:
    """Re-asserting busy must not call setRange again (keeps animation smooth)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    bar = make_thin_busy_progress()
    set_thin_busy_progress_active(bar, True)
    assert bar.maximum() == 0
    # Spy: if setRange were called with (0,0) again Qt would restart the chunk.
    calls: list[tuple[int, int]] = []
    original = bar.setRange

    def _track(lo: int, hi: int) -> None:
        calls.append((lo, hi))
        original(lo, hi)

    bar.setRange = _track  # type: ignore[method-assign]
    set_thin_busy_progress_active(bar, True)
    set_thin_busy_progress_active(bar, True)
    assert calls == []
    set_thin_busy_progress_active(bar, False)
    assert calls == [(0, 1)]
    _ = app
