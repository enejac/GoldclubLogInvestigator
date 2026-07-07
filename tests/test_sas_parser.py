"""SAS 6F TX/RX parser (extended meters)."""

from __future__ import annotations

from network import sas_parser


def test_parse_sas_6f_paste_uses_bcd_not_raw_hex() -> None:
    from gui.sas_verify_dialog import decode_sas_bcd, parse_sas_6f_paste

    assert decode_sas_bcd(bytes.fromhex("090673")) == "90673"
    # Mis-framed bytes must not produce hex garbage like "16f62".
    assert decode_sas_bcd(bytes.fromhex("016f62")) == "0"


def test_parse_sas_6f_paste_cashless_transfer_to_host() -> None:
    from gui.sas_verify_dialog import parse_sas_6f_paste

    from tests.test_sas_parser import _RX_CASHLESS

    rows = {r.meter_id: r.sas_value_text for r in parse_sas_6f_paste(_RX_CASHLESS)}
    assert rows["0018"] == "18000"
    assert rows["0017"] == "862000"


def test_build_verify_6f_rows_from_paste_full_template() -> None:
    from gui.sas_verify_dialog import build_verify_6f_rows_from_paste
    from network.sas_serial_meters import DEFAULT_6F_VERIFY_POLL_CODES

    line = "RX<= 01 6F 00 01 00 00 00 02 00 34"
    rows = build_verify_6f_rows_from_paste(line)
    assert len(rows) == len(DEFAULT_6F_VERIFY_POLL_CODES)
    by_id = {r.meter_id: r.sas_value_text for r in rows}
    assert by_id["0000"] == "34"
    assert by_id["0005"] == ""


def test_parse_sas_6f_paste_coin_in_simple() -> None:
    from gui.sas_verify_dialog import parse_sas_6f_paste

    line = "RX<= 01 6F 00 01 00 00 00 02 00 34"
    rows = {r.meter_id: r.sas_value_text for r in parse_sas_6f_paste(line)}
    assert rows["0000"] == "34"


def test_wire_meter_code_to_6f_verify_code() -> None:
    from gui.sas_verify_dialog import wire_meter_code_to_6f_verify_code

    assert wire_meter_code_to_6f_verify_code("1800") == "0018"
    assert wire_meter_code_to_6f_verify_code("0500") == "0005"
    assert wire_meter_code_to_6f_verify_code("0000") == "0000"


def test_normalize_com_port() -> None:
    from network.sas_serial_meters import normalize_com_port

    assert normalize_com_port("com11") == "COM11"
    assert normalize_com_port("11") == "COM11"
    assert normalize_com_port(" COM3 ") == "COM3"


def test_pick_sas_com_port() -> None:
    from network.sas_serial_meters import SerialPortInfo, pick_sas_com_port

    ports = [
        SerialPortInfo("COM3", "USB Serial Port"),
        SerialPortInfo("COM5", "Communications Port"),
    ]
    assert pick_sas_com_port("COM11", ports) == "COM3"
    assert pick_sas_com_port("COM3", ports) == "COM3"
    assert pick_sas_com_port("COM11", [SerialPortInfo("COM11", "FTDI USB Serial")]) == "COM11"
    assert pick_sas_com_port("COM11", [SerialPortInfo("COM7", "USB Serial Port")]) == "COM7"
    assert pick_sas_com_port("", [SerialPortInfo("COM4", "USB Serial Port")]) == "COM4"


def test_auto_wire_baud_combos_prefers_raw_19200() -> None:
    from network.sas_serial_meters import AUTO_WIRE_BAUD_COMBOS, _probe_should_continue

    assert AUTO_WIRE_BAUD_COMBOS[0] == ("raw", 19200)
    assert _probe_should_continue("No bytes were received (batch 1).")
    assert _probe_should_continue("SAS link not responding on COM4")
    assert not _probe_should_continue("Could not open COM4 for SAS meter fetch.")


def test_general_poll_alternation() -> None:
    from network.sas_serial_meters import _general_poll_alternation

    assert _general_poll_alternation(1) == (0x81, 0x80)
    assert _general_poll_alternation(2) == (0x82, 0x83)


def test_build_igt_tester_6f_poll_frame() -> None:
    from network.sas_serial_meters import (
        IGT_TESTER_6F_POLL_BATCHES,
        _extract_6f_response_frame,
        build_igt_tester_6f_poll_frame,
        format_sas_traffic_line,
        mux_decode_all,
        mux_encode_general_poll,
        mux_encode_sas_frame,
    )
    from network.sas_parser import parse_rx_response_ordered

    tx = build_igt_tester_6f_poll_frame(["0000"])
    assert tx[:6].hex() == "016f04000000"
    batch1 = build_igt_tester_6f_poll_frame(IGT_TESTER_6F_POLL_BATCHES[0])
    assert (
        format_sas_traffic_line("TX>=", batch1)
        == "TX>= 01 6F 10 00 00 05 00 06 00 07 00 00 00 01 00 1C 00 1D 00 4C 82"
    )
    mux_wire = mux_encode_sas_frame(batch1)
    assert mux_wire.hex().upper() == (
        "1B80011B006F100000050006000700000001001C001D004C82"
    )
    assert mux_encode_general_poll(0x81).hex().upper() == "1B8081"
    decoded = mux_decode_all(mux_wire)
    assert decoded == [batch1]
    rx_line = "RX<= 01 6F 00 01 00 00 00 02 00 34"
    assert parse_rx_response_ordered(rx_line) == [("0000", 34)]
    from tests.test_sas_parser import _RX_CASHLESS

    rx_bytes = bytes.fromhex(_RX_CASHLESS.replace("RX<= ", "").replace(" ", ""))
    got = _extract_6f_response_frame(rx_bytes)
    assert got[0] == 0x01 and got[1] == 0x6F
    assert "1800" in dict(parse_rx_response_ordered(format_sas_traffic_line("RX<=", got)))


def test_parse_rx_response_ordered_coin_in() -> None:
    line = "RX<= 01 6F 00 01 00 00 00 02 00 34"
    got = sas_parser.parse_rx_response_ordered(line)
    assert got == [("0000", 34)]


def test_parse_rx_response_meters_dict() -> None:
    line = "RX<= 01 6F 00 01 00 00 00 02 00 34"
    d = sas_parser.parse_rx_response_meters(line)
    assert d["0000"] == 34


def test_last_rx_code_values_multiline() -> None:
    text = (
        "Mon Jan 01 00:00:00 2026\n"
        "RX<= 01 6F 00 01 00 00 00 02 00 10\n"
        "Tue Jan 02 00:00:00 2026\n"
        "RX<= 01 6F 00 01 00 01 00 02 00 99\n"
    )
    d = sas_parser.last_rx_code_values_from_text(text)
    assert d == {"0100": 99}


def test_pair_tx_rx_blocks() -> None:
    lines = [
        "Mon Jan 01 00:00:00 2026",
        "TX>= 01 6F 00 01 00 01 00 02 00 00",
        "RX<= 01 6F 00 01 00 01 00 02 00 55",
    ]
    pairs = sas_parser.pair_tx_rx_blocks(lines)
    assert len(pairs) == 1
    tx_codes, rx_d = pairs[0]
    assert tx_codes == ["0100"]
    assert rx_d["0100"] == 55


def test_code_to_display_name_known() -> None:
    assert sas_parser.code_to_display_name("0000") == "Total Coin In (0000)"
    assert sas_parser.code_to_display_name("0300") == "TotalHandPaidCancelled (0300)"
    assert sas_parser.code_to_display_name("1F00") == "TotalAttendantPaidPayTableWin (1F00)"


# --- Real IGT-tester multi-meter frames (wire-order codes, variable length) ---

_RX_CASHLESS = (
    "RX<= 01 6F 62 00 00 17 00 09 00 00 00 00 00 00 86 20 00 A0 00 09 00 00 00 "
    "00 00 00 85 00 00 A2 00 09 00 00 00 00 00 00 01 00 00 A4 00 09 00 00 00 00 "
    "00 00 00 20 00 18 00 09 00 00 00 00 00 00 01 80 00 B8 00 09 00 00 00 00 00 "
    "00 01 00 00 BA 00 09 00 00 00 00 00 00 00 80 00 BC 00 09 00 00 00 00 00 00 "
    "00 00 00 80 82"
)

# Frame containing a 0-length meter (6E00) mid-stream; parser must not desync.
_RX_CREDIT = (
    "RX<= 01 6F 4D 00 00 0B 00 09 00 00 00 00 00 00 00 10 00 17 00 09 00 00 00 "
    "00 00 00 86 20 00 15 00 09 00 00 00 00 00 00 00 83 40 6E 00 00 23 00 09 00 "
    "00 00 00 00 02 53 17 86 16 00 09 00 00 00 00 01 00 22 76 50 18 00 09 00 00 "
    "00 00 00 00 01 80 00 53 67"
)

_RX_HANDPAY_CANCELLED = (
    "RX<= 01 6F 3E 00 00 03 00 09 00 00 00 00 00 02 53 17 86 02 00 09 00 "
    "00 00 00 00 00 00 00 00 1F 00 09 00 00 00 00 00 00 00 00 00 20 00 09 "
    "00 00 00 00 00 00 00 00 00 04 00 09 00 00 00 00 01 02 77 74 36 BD F3"
)


def test_cashless_frame_wire_order_codes_and_values() -> None:
    d = sas_parser.parse_rx_response_meters(_RX_CASHLESS)
    assert d == {
        "1700": 862000,
        "A000": 850000,
        "A200": 10000,
        "A400": 2000,
        "1800": 18000,
        "B800": 10000,
        "BA00": 8000,
        "BC00": 0,
    }


def test_cashless_total_reconciles_with_components() -> None:
    d = sas_parser.parse_rx_response_meters(_RX_CASHLESS)
    assert d["1700"] == d["A000"] + d["A200"] + d["A400"]  # transfer to EGM
    assert d["1800"] == d["B800"] + d["BA00"] + d["BC00"]  # transfer to host


def test_zero_length_meter_does_not_desync() -> None:
    ordered = sas_parser.parse_rx_response_ordered(_RX_CREDIT)
    codes = [c for c, _ in ordered]
    assert codes == ["0B00", "1700", "1500", "6E00", "2300", "1600", "1800"]
    d = dict(ordered)
    assert d["6E00"] == 0  # device reported 6E00 with length 0
    assert d["1700"] == 862000  # consistent with the cashless frame
    assert d["1500"] == 8340


def test_handpay_cancelled_frame_names_and_values() -> None:
    ordered = sas_parser.parse_rx_response_ordered(_RX_HANDPAY_CANCELLED)
    assert ordered == [
        ("0300", 2531786),
        ("0200", 0),
        ("1F00", 0),
        ("2000", 0),
        ("0400", 102777436),
    ]
    labels = [sas_parser.code_to_display_name(code) for code, _ in ordered]
    assert labels == [
        "TotalHandPaidCancelled (0300)",
        "TotalJackpot (0200)",
        "TotalAttendantPaidPayTableWin (1F00)",
        "TotalAttendantPaidProg.Win (2000)",
        "TotalCancelledCredits (0400)",
    ]
