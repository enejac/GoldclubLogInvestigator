"""IGT Message Display paste + persistent SAS link sync budgets."""

from __future__ import annotations


def test_parse_sas_6f_paste_igt_message_display() -> None:
    from gui.sas_verify_dialog import parse_sas_6f_paste

    text = (
        "$6F = Meter 1 Code     = 0500\n"
        "$6F = Meter 1          = 000000000000000002\n"
        "$6F = Meter 4 Code     = 0000\n"
        "$6F = Meter 4          = 000000000000001100\n"
        "$6F = Meter 5 Code     = 0400\n"
        "$6F = Meter 5          = 000000000000016F3E\n"
    )
    rows = {r.meter_id: r.sas_value_text for r in parse_sas_6f_paste(text)}
    assert rows["0005"] == "2"
    assert rows["0000"] == "1100"
    # Hex letters in the IGT value field are corrupt BCD, not base-16 credits.
    assert rows["0004"] == ""


def test_inter_batch_gap_holds_the_link_without_idling_it() -> None:
    """Two GP polls between 6F batches: measured floor on .90.

    One poll made the link miss a batch, and the retry cost more than the
    polls saved (8.5 s vs 5.9 s), so this must not drop below two.
    """
    import inspect

    from network.sas_serial_meters import (
        DEFAULT_IGT_INTER_BATCH_POLLS,
        _inter_poll_between_6f_batches,
    )

    assert DEFAULT_IGT_INTER_BATCH_POLLS == 2
    # The between-batch call site passes no poll_count, so the default carries it.
    default = inspect.signature(_inter_poll_between_6f_batches).parameters["poll_count"].default
    assert default == DEFAULT_IGT_INTER_BATCH_POLLS


def test_igt_link_sync_polls_are_persistent() -> None:
    from network.sas_serial_meters import (
        DEFAULT_IGT_LINK_SYNC_POLLS,
        DEFAULT_IGT_LINK_SYNC_POLLS_PERSISTENT,
        DEFAULT_IGT_LINK_SYNC_POLLS_QUICK,
    )

    assert DEFAULT_IGT_LINK_SYNC_POLLS >= 40
    assert DEFAULT_IGT_LINK_SYNC_POLLS_QUICK >= 25
    assert DEFAULT_IGT_LINK_SYNC_POLLS_PERSISTENT >= 100


def test_warm_capture_skips_the_gp_wakeup_flood() -> None:
    """Auto fetch re-captures constantly; only the first one needs the wakeup.

    The flood is ~3 s of dead time on a link that already answered $6F, and on
    lab .90 the whole allowance is always spent because GP never stabilises.
    """
    from network.sas_serial_meters import (
        DEFAULT_IGT_LINK_SYNC_POLLS_PERSISTENT,
        DEFAULT_IGT_LINK_SYNC_POLLS_PREFETCH,
        DEFAULT_IGT_LINK_SYNC_POLLS_WARM,
        DEFAULT_IGT_POLL_INTERVAL_S,
        link_sync_polls_for_capture,
    )

    warm = link_sync_polls_for_capture(quick=True, warm=True)
    cold_quick = link_sync_polls_for_capture(quick=True, warm=False)
    full = link_sync_polls_for_capture(quick=False, warm=False)

    assert warm == DEFAULT_IGT_LINK_SYNC_POLLS_WARM
    assert cold_quick == DEFAULT_IGT_LINK_SYNC_POLLS_PREFETCH
    assert full == DEFAULT_IGT_LINK_SYNC_POLLS_PERSISTENT
    # Warm still polls (a couple of GPs keep the cadence) but under a second.
    assert 0 < warm * DEFAULT_IGT_POLL_INTERVAL_S <= 1.0
    assert warm < cold_quick < full
    # Warm wins back at least two seconds of every Auto fetch round.
    assert (cold_quick - warm) * DEFAULT_IGT_POLL_INTERVAL_S >= 2.0


def test_full_timing_refresh_is_never_warm() -> None:
    """"Refresh (full timing)" is an explicit try-hard — it keeps the cold budget."""
    import inspect

    from gui.sas_verify_dialog import MeterFetchWorker, SasVerifyDialog

    assert "warm" in inspect.signature(MeterFetchWorker.__init__).parameters
    src = inspect.getsource(SasVerifyDialog._begin_meter_fetch)
    assert "warm=not full_timing" in src


def test_fetch_meters_continues_to_6f_when_gp_sync_incomplete(monkeypatch) -> None:
    """IGT Quick Commands: send $6F even when GP sync never sees a stable reply."""
    from network import sas_serial_meters as m

    calls: list[str] = []

    class _FakeSer:
        def close(self) -> None:
            return None

    class _FakeWire:
        def __init__(self, *a, **k) -> None:
            return None

        def send_frame(self, tx: bytes) -> None:
            calls.append("6f")

        def send_general_poll(self, b: int) -> None:
            calls.append("gp")

    monkeypatch.setattr(m, "_require_pyserial", lambda: object())
    monkeypatch.setattr(
        m, "_open_serial_with_retry", lambda *a, **k: (_FakeSer(), "COM4")
    )
    monkeypatch.setattr(m, "SasWire", _FakeWire)
    monkeypatch.setattr(m, "_sync_sas_link_igt", lambda *a, **k: (False, b"\x00"))
    monkeypatch.setattr(m, "_inter_poll_between_6f_batches", lambda *a, **k: None)
    # One successful 6F frame then stop (partial ok).
    frame = bytes.fromhex("01 6F 00 01 00 05 00 00 00 02 00 34")
    # Make a plausible framed response the reader accepts — patch _read_6f_response.
    monkeypatch.setattr(
        m,
        "_read_6f_response",
        lambda *a, **k: (frame, frame, 0.1),
    )
    monkeypatch.setattr(
        m,
        "build_igt_tester_6f_poll_frame",
        lambda batch, address=0x01: b"\x01\x6f" + bytes([len(batch)]) + b"\x00\x00",
    )
    monkeypatch.setattr(m, "format_sas_traffic_line", lambda d, p: f"{d} {p.hex()}")
    monkeypatch.setattr(m, "parse_6f_meter_values_from_paste", lambda t: {"0005": "2"})
    monkeypatch.setattr(m, "catalog_bill_out_rows", lambda: ())

    result = m._fetch_meters_once(
        port="COM4",
        baud=19200,
        force_capture=True,
        skip_bill_polls=True,
        link_sync_polls=4,
        poll_batches=(("0005",), ("0006",)),
    )
    assert "6f" in calls
    assert "Quick-Command" in (result.paste_text or "") or "GP sync incomplete" in (
        result.paste_text or ""
    )
    assert result.paste_text



def test_fetch_meters_accepts_link_sync_polls_kwarg() -> None:
    import inspect

    from network.sas_serial_meters import fetch_meters_over_serial

    params = inspect.signature(fetch_meters_over_serial).parameters
    assert "link_sync_polls" in params
