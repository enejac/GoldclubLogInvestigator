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


def test_extract_6f_finds_frame_after_leading_noise() -> None:
    from network.sas_serial_meters import _extract_6f_response_frame

    frame = bytes.fromhex(
        "016f620000170009000000000000600000a00009000000000000500000"
        "a20009000000000000000000a40009000000000000100000180009000000000000000000"
        "b80009000000000000000000ba0009000000000000000000bc00090000000000000000006040"
    )
    got = _extract_6f_response_frame(frame, address=1)
    assert got == frame
    assert _extract_6f_response_frame(b"\xff\xff" + frame, address=1) == frame


def test_fetch_meters_once_mock_serial_single_batch() -> None:
    from unittest.mock import patch

    from network.sas_serial_meters import (
        _extract_6f_response_frame,
        _fetch_meters_once,
        append_sas_crc,
        build_igt_tester_6f_poll_frame,
    )

    body = bytes.fromhex("016f00")
    sample_rx = append_sas_crc(body)
    assert _extract_6f_response_frame(sample_rx) == sample_rx

    class ScriptedSerial:
        def __init__(self) -> None:
            self._rx = bytearray()
            self._tx = bytearray()
            self.timeout = 0.05
            self.parity = None
            self.dtr = False
            self.rts = False
            self._closed = False

        @property
        def in_waiting(self) -> int:
            return len(self._rx)

        @property
        def out_waiting(self) -> int:
            return 0

        def read(self, size: int = 1) -> bytes:
            if not self._rx:
                return b""
            n = len(self._rx) if size is None or size < 0 else min(int(size), len(self._rx))
            out = bytes(self._rx[:n])
            del self._rx[:n]
            return out

        def write(self, data: bytes) -> int:
            self._tx.extend(data)
            if len(self._tx) == 1 and self._tx[0] in (0x80, 0x81):
                self._rx.extend(bytes([0x01, 0xFF, 0x7E, 0x00]))
                self._tx.clear()
            elif len(self._tx) >= 3 and self._tx[0] == 0x01 and self._tx[1] == 0x6F:
                expected = build_igt_tester_6f_poll_frame(["0000"])
                if bytes(self._tx) == expected:
                    self._rx.extend(sample_rx)
                    self._tx.clear()
            return len(data)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            self._closed = True

    fake = ScriptedSerial()
    with patch(
        "network.sas_serial_meters._open_serial_with_retry",
        return_value=(fake, "COM99"),
    ):
        result = _fetch_meters_once(
            port="COM99",
            baud=19200,
            poll_batches=(("0000",),),
            timeout_s=2.0,
            port_wait_s=0.5,
            force_capture=True,
            skip_bill_polls=True,
        )
    assert fake._closed
    assert result.port_used == "COM99"
    assert "TX>=" in result.paste_text
    assert "RX<=" in result.paste_text
    assert "01 6F" in result.paste_text
    assert result.rx_frame[1] == 0x6F


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


def test_bill_in_long_poll_parse() -> None:
    from network.sas_serial_meters import (
        append_sas_crc,
        build_simple_long_poll,
        crc16_kermit,
        decode_bill_bcd_count,
        parse_bill_in_response_frame,
        parse_sas_bill_paste,
    )

    tx = build_simple_long_poll(address=0x01, cmd=0x33)
    assert tx[:2] == bytes([0x01, 0x33])
    assert crc16_kermit(tx[:-2]) == (tx[-2] | (tx[-1] << 8))
    body = bytes([0x01, 0x33, 0x00, 0x00, 0x00, 0x01])
    rx = append_sas_crc(body)
    assert parse_bill_in_response_frame(rx, address=0x01, cmd=0x33) == 1
    assert decode_bill_bcd_count(body[2:6]) == 1
    paste = (
        "TX>= " + " ".join(f"{b:02X}" for b in tx) + "\n"
        "RX<= " + " ".join(f"{b:02X}" for b in rx) + "\n"
    )
    rows = parse_sas_bill_paste(paste)
    assert len(rows) == 1
    assert rows[0].sas_cmd == 0x33
    assert rows[0].count == 1
    assert rows[0].amount_cents == 500


def test_build_aggregate_bill_rows_from_6f_000b() -> None:
    from network.sas_serial_meters import build_aggregate_bill_rows, parse_6f_meter_values_from_paste

    paste = (
        "RX<= 01 6F 4D 00 00 0B 00 09 00 00 00 00 00 00 06 00 "
        "17 00 09 00 00 00 00 00 00 01 00 00 23 42"
    )
    values = parse_6f_meter_values_from_paste(paste)
    assert values.get("000B") == "600"
    rows = build_aggregate_bill_rows(
        paste_text=paste,
        machine_state={"notesinstackercnt": "2", "notesinstackeramt": "600"},
    )
    assert len(rows) == 1
    assert rows[0].label == "Total Bills In"
    assert rows[0].sas_code == "000B"
    assert rows[0].amount_cents == 600
    assert rows[0].count == 2
    assert rows[0].source == "aggregate"


def test_align_bills_in_sas_credits_maps_dollars_to_credits() -> None:
    from network.meter_comparator import align_bills_in_sas_credits, bills_in_meters_match

    assert align_bills_in_sas_credits("6", "600") == "600"
    assert align_bills_in_sas_credits("600", "600") == "600"
    assert align_bills_in_sas_credits("6", "601") == "6"
    assert align_bills_in_sas_credits("209", "20960") == "20960"
    assert align_bills_in_sas_credits("209", "20900") == "20900"
    assert align_bills_in_sas_credits("1769", "176930") == "176930"
    assert bills_in_meters_match("209", "20960")
    assert bills_in_meters_match("1769", "176930")
    assert not bills_in_meters_match("6", "601")


def test_0b00_len8_reports_whole_dollar_bcd() -> None:
    ordered = sas_parser.parse_rx_response_ordered(
        "RX<= 01 6F 0D 00 00 0B 00 08 00 00 00 00 00 00 00 06 "
        "17 00 09 00 00 00 00 00 00 01 00 00 23 42"
    )
    assert dict(ordered)["0B00"] == 6


def test_merge_bill_rows_with_catalog_fills_missing_denoms() -> None:
    from network.sas_serial_meters import (
        SasBillDenomRow,
        merge_bill_rows_with_catalog,
        paste_has_bill_lp_attempts,
    )

    responded = (
        SasBillDenomRow(
            sas_cmd=0x33,
            label="$5.00",
            face_cents=500,
            enabled=True,
            count=1,
            amount_cents=500,
        ),
    )
    rows = merge_bill_rows_with_catalog(responded)
    assert len(rows) == 7
    assert rows[2].label == "$5.00"
    assert rows[2].count == 1
    assert rows[0].count == 0
    assert rows[0].source == "missing"
    paste = "TX>= 01 31 D2 39\nRX<= (no response - bill $1.00 LP 31)"
    assert paste_has_bill_lp_attempts(paste)


def test_build_bill_display_rows_uses_full_catalog_after_lp_attempts() -> None:
    from network.sas_serial_meters import build_bill_display_rows

    paste = (
        "; --- Bill-in long polls ($31-$45, enabled only) ---\n"
        "TX>= 01 31 D2 39\n"
        "RX<= (no response - bill $1.00 LP 31)\n"
    )
    rows = build_bill_display_rows(bill_rows=(), paste_text=paste)
    assert len(rows) == 7
    assert all(r.count == 0 for r in rows)


def test_build_bill_display_rows_empty_fallback_catalog() -> None:
    from network.sas_serial_meters import build_bill_display_rows

    rows = build_bill_display_rows(bill_rows=(), paste_text="RX<= 01 6F 55 00 00 05 00 09")
    assert len(rows) == 7
    assert rows[0].label == "$1.00"
    assert rows[0].count == 0


def test_build_bill_rows_from_cabinet_note_meters() -> None:
    from network.accounting_state_loader import extract_cabinet_bill_note_meters
    from network.sas_serial_meters import build_bill_rows_from_cabinet_note_meters, build_bill_display_rows

    state = {
        "note_curInCnt_500": "6",
        "note_curInAmt_500": "3000",
        "note_curInCnt_10000": "11",
        "note_curInAmt_10000": "110000",
    }
    by_face = extract_cabinet_bill_note_meters(state)
    assert by_face[500]["count"] == 6
    assert by_face[500]["amount_cents"] == 3000
    rows = build_bill_rows_from_cabinet_note_meters(state)
    assert len(rows) == 2
    five = next(r for r in rows if r.label == "$5.00")
    assert five.count == 6
    assert five.amount_cents == 3000
    assert five.source == "cabinet"
    display = build_bill_display_rows(machine_state=state)
    assert len(display) == 7
    assert next(r for r in display if r.label == "$5.00").count == 6
    assert all(r.count == 0 for r in display if r.label == "$1.00")


def test_expand_aggregate_bill_assigns_single_denom_row() -> None:
    from gui.sas_verify_dialog import prepare_bill_table_body_rows
    from network.sas_serial_meters import SasBillDenomRow, build_aggregate_bill_rows

    paste = (
        "RX<= 01 6F 4D 00 00 0B 00 09 00 00 00 00 00 00 00 05 00 "
        "17 00 09 00 00 00 00 00 00 01 00 00 23 42"
    )
    agg_rows = build_aggregate_bill_rows(
        paste_text=paste,
        machine_state={"notesinstackercnt": "1", "notesinstackeramt": "500"},
    )
    body, totals = prepare_bill_table_body_rows(agg_rows, direction="in")
    assert totals is None
    five = next(r for r in body if r.label == "$5.00")
    assert five.count == 1
    assert five.amount_cents == 500
    assert all(r.count == 0 for r in body if r.label != "$5.00")


def test_build_bill_out_display_rows_full_catalog() -> None:
    from network.sas_serial_meters import build_bill_out_display_rows

    rows = build_bill_out_display_rows()
    assert len(rows) == 7
    assert rows[0].direction == "out"
    assert rows[0].count == 0


def test_build_aggregate_bill_rows_aligns_dollar_sas_000b() -> None:
    from network.sas_serial_meters import build_aggregate_bill_rows

    paste = (
        "RX<= 01 6F 0D 00 00 0B 00 08 00 00 00 00 00 00 00 06 "
        "17 00 09 00 00 00 00 00 00 01 00 00 23 42"
    )
    rows = build_aggregate_bill_rows(
        paste_text=paste,
        machine_state={"notesinstackercnt": "2", "notesinstackeramt": "600"},
    )
    assert len(rows) == 1
    assert rows[0].amount_cents == 600


def test_should_skip_cabinet_reload_when_loaded_and_same_root() -> None:
    from gui.sas_verify_dialog import should_skip_cabinet_reload

    root = r"\\10.0.0.90\c$\Goldclub\var\log"
    assert should_skip_cabinet_reload(
        scan_root=root,
        loaded_scan_root=root,
        machine_state_loaded=True,
    )


def test_meter_fetch_display_action_capture_when_cached() -> None:
    from gui.sas_verify_dialog import meter_fetch_display_action

    assert (
        meter_fetch_display_action(
            cached_result=object(),
            fetch_running=False,
        )
        == "capture"
    )


def test_meter_fetch_display_action_capture_after_user_applied() -> None:
    from gui.sas_verify_dialog import meter_fetch_display_action

    assert (
        meter_fetch_display_action(
            cached_result=object(),
            fetch_running=False,
            user_already_applied=True,
        )
        == "capture"
    )


def test_meter_fetch_display_action_capture_fallback() -> None:
    from gui.sas_verify_dialog import meter_fetch_display_action

    assert (
        meter_fetch_display_action(
            cached_result=None,
            fetch_running=False,
        )
        == "capture"
    )


def test_format_bill_amount_display() -> None:
    from gui.sas_verify_dialog import format_bill_amount_display

    assert format_bill_amount_display(100) == "1.00"
    assert format_bill_amount_display(2200) == "22.00"
    assert format_bill_amount_display(20800) == "208.00"
    assert format_bill_amount_display(0) == "0.00"


def test_format_bill_reject_count_label() -> None:
    from gui.sas_verify_dialog import format_bill_reject_count_label

    assert format_bill_reject_count_label("0") == "BILL REJECT COUNT 0"
    assert format_bill_reject_count_label("") == "BILL REJECT COUNT"
    assert format_bill_reject_count_label(None) == "BILL REJECT COUNT"


def test_prepare_bill_table_body_rows_expands_aggregate() -> None:
    from gui.sas_verify_dialog import prepare_bill_table_body_rows
    from network.sas_serial_meters import SasBillDenomRow

    agg = SasBillDenomRow(
        sas_cmd=0,
        label="Total Bills In",
        face_cents=0,
        enabled=True,
        count=17,
        amount_cents=20800,
        sas_code="000B",
        source="aggregate",
        direction="in",
    )
    body, totals = prepare_bill_table_body_rows([agg], direction="in")
    assert len(body) == 7
    assert totals == {"count": 17, "amount_cents": 20800}
    assert all(r.count == 0 for r in body)


def test_verify_bills_table_spec_ok() -> None:
    from gui.sas_verify_dialog import _BILLS_TABLE_HEADERS, verify_bills_table_spec

    assert verify_bills_table_spec(
        column_count=3,
        headers=_BILLS_TABLE_HEADERS,
        vertical_header_hidden=True,
        row_count=8,
    ) == []


def test_verify_bills_table_spec_detects_issues() -> None:
    from gui.sas_verify_dialog import verify_bills_table_spec

    issues = verify_bills_table_spec(
        column_count=6,
        headers=("Bill", "Amount", "Count"),
        vertical_header_hidden=False,
    )
    assert any("3 columns" in i for i in issues)
    assert any("headers" in i for i in issues)
    assert any("vertical" in i for i in issues)


def test_format_master_amount_display() -> None:
    from gui.sas_verify_dialog import format_master_amount_display

    assert format_master_amount_display("20800") == "$208.00"
    assert format_master_amount_display("500") == "$5.00"
    assert format_master_amount_display("0") == "$0.00"
    assert format_master_amount_display("") == "$0.00"


def test_compute_master_summary_reference_totals() -> None:
    from gui.sas_verify_dialog import compute_master_summary

    values = {
        "000B": "20800",
        "0000": "0",
        "0017": "500",
        "0015": "500",
        "0023": "500",
        "006E": "0",
        "0001": "0",
        "0003": "21555",
        "0016": "0",
        "0018": "0",
    }
    totals = compute_master_summary(values)
    assert abs(totals["credit_in"] - 223.0) < 0.01
    assert abs(totals["credit_out"] - 215.55) < 0.01
    assert abs(totals["total_credit"] - 7.45) < 0.01
    assert totals["inout_pct"] is not None
    assert abs(totals["inout_pct"] - 96.66) < 0.05


def test_format_transfer_count_display() -> None:
    from gui.sas_verify_dialog import format_transfer_count_display, lookup_normalized_machine_value

    assert format_transfer_count_display("") == "0"
    assert format_transfer_count_display("12") == "12"
    assert format_transfer_count_display("1,234") == "1234"
    state = {"watTransferInCnt": "7", "TicketInCnt": "3"}
    assert lookup_normalized_machine_value(state, "wattransferincnt") == "7"
    assert lookup_normalized_machine_value(state, "missing") == ""


def test_verify_master_tab_spec_ok() -> None:
    from gui.sas_verify_dialog import MASTER_TRACKED_CODES, verify_master_tab_spec

    assert verify_master_tab_spec(
        group_titles=("TOTAL CREDIT", "HANDPAY OUT", "WAGERED CREDITS"),
        value_label_codes=frozenset(MASTER_TRACKED_CODES),
        has_credit_section_totals=True,
    ) == []


def test_compute_game_summary_reference_totals() -> None:
    from gui.sas_verify_dialog import compute_game_summary, format_game_pct_display

    totals = compute_game_summary(
        played_raw="3",
        won_raw="1",
        lost_raw="0",
        bet_raw="170",
        win_raw="200",
    )
    assert totals["played"] == 3
    assert totals["won"] == 1
    assert totals["lost"] == 2
    assert totals["bet"] == 1.70
    assert totals["win"] == 2.00
    assert abs(float(totals["bet_minus_win"]) - (-0.30)) < 0.001
    assert abs(float(totals["yield_pct"]) - 117.647) < 0.05
    assert abs(float(totals["hold_pct"]) - (-17.647)) < 0.05
    assert format_game_pct_display(float(totals["yield_pct"])) == "117.65%"


def test_verify_game_tab_spec_ok() -> None:
    from gui.sas_verify_dialog import GAME_RESIDUAL_ROWS, verify_game_tab_spec

    residual_keys = frozenset(k.lower().replace(" ", "_") for k, _ in GAME_RESIDUAL_ROWS)
    assert verify_game_tab_spec(
        group_titles=(
            "Performance Meters",
            "Residual Credit Removal Feature",
            "Machine Yield Chart",
        ),
        perf_label_keys=frozenset(
            {
                "played",
                "won",
                "lost",
                "bet",
                "win",
                "game_win",
                "bonus_win",
                "sas_bonus",
                "prog_win",
                "bet_minus_win",
                "yield",
                "hold",
            }
        ),
        residual_label_keys=residual_keys,
        has_yield_chart=True,
    ) == []


def test_build_yield_chart_slices_reference() -> None:
    from gui.machine_yield_chart import build_yield_chart_slices, format_yield_chart_pct

    slices = build_yield_chart_slices(69.45, 30.55)
    assert len(slices) == 2
    assert abs(slices[0].pct - 69.45) < 0.001
    assert abs(slices[1].pct - 30.55) < 0.001
    assert format_yield_chart_pct(69.45) == "69.45 %"


def test_build_yield_chart_slices_empty_without_bet() -> None:
    from gui.machine_yield_chart import build_yield_chart_slices

    assert build_yield_chart_slices(None, None) == ()


def test_pie_geometry_centered_in_pie_rect() -> None:
    from PySide6.QtCore import QRectF

    from gui.machine_yield_chart import chart_layout, pie_geometry

    rect = QRectF(0, 0, 300, 220)
    pie_rect, legend_rect = chart_layout(rect)
    cx, cy, rx, ry, _, _ = pie_geometry(pie_rect)
    assert pie_rect.right() < legend_rect.left() + 2
    assert abs(cx - pie_rect.center().x()) < 0.01
    assert rx > 0 and ry > 0


def test_center_widget_in_panel_top_stuck_layout() -> None:
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    from gui.sas_verify_dialog import (
        center_widget_in_panel,
        meter_tab_outer_layout,
        verify_center_widget_in_panel_layout,
    )

    app = QApplication.instance() or QApplication([])
    body = QWidget()
    layout = meter_tab_outer_layout(body)
    cluster = QWidget()
    center_widget_in_panel(layout, cluster)
    assert verify_center_widget_in_panel_layout(layout) == []
    assert layout.itemAt(0).layout() is not None
    assert layout.itemAt(layout.count() - 1).spacerItem() is not None


def test_parse_game_catalog_theme_ids() -> None:
    from network.accounting_state_loader import parse_game_catalog_entries, parse_game_catalog_theme_ids

    xml = """<?xml version="1.0"?>
    <GameCatalogSettings>
      <GameSelectorButton><Id>PR3_RedZone</Id><internalID>PR3_RedZone</internalID></GameSelectorButton>
      <GameSelectorButton><Id>Sizzling Sevens HD HnW</Id><ThemePath>SizzlingSevensHD_HnW</ThemePath></GameSelectorButton>
      <GameSelectorButton><ThemePath>RouletteGame</ThemePath></GameSelectorButton>
    </GameCatalogSettings>"""
    assert parse_game_catalog_theme_ids(xml) == [
        "PR3_RedZone",
        "Sizzling Sevens HD HnW",
        "RouletteGame",
    ]
    assert parse_game_catalog_entries(xml)[1] == (
        "Sizzling Sevens HD HnW",
        "SizzlingSevensHD_HnW",
    )


def test_extract_theme_perf_meters_from_xml(tmp_path) -> None:
    from network.accounting_state_loader import extract_theme_perf_meters_from_xml

    xml = """<?xml version="1.0"?>
    <root xmlns:d4p1="urn:test">
      <d4p1:perfMeter d4p1:meterName="coinIn" d4p1:themeId="Roulette Game" d4p1:paytableId="return_97_0" d4p1:meterValue="170"/>
      <d4p1:perfMeter d4p1:meterName="gamesPlayed" d4p1:themeId="Roulette Game" d4p1:paytableId="return_97_0" d4p1:meterValue="3"/>
      <d4p1:perfMeter d4p1:meterName="coinIn" d4p1:themeId="PR3_RedZone" d4p1:paytableId="return_94_0" d4p1:meterValue="50"/>
      <d4p1:perfMeter d4p1:meterName="coinIn" d4p1:themeId="" d4p1:paytableId="return_99_0" d4p1:meterValue="999"/>
    </root>"""
    p = tmp_path / "DeviceManagerData.xml_1"
    p.write_text(xml, encoding="utf-8")
    meters = extract_theme_perf_meters_from_xml(p)
    assert meters["Roulette Game"]["return_97_0"]["coinin"] == "170"
    assert meters["Roulette Game"]["return_97_0"]["gamesplayed"] == "3"
    assert meters["PR3_RedZone"]["return_94_0"]["coinin"] == "50"
    assert "" not in meters


def test_parse_math_settings_paytable_ids() -> None:
    from network.accounting_state_loader import parse_math_settings_paytable_ids

    xml = "<root><MathFile>return_94_0.thm</MathFile><Other>return_92_0</Other></root>"
    assert parse_math_settings_paytable_ids(xml) == ["return_92_0", "return_94_0"]


def test_aggregate_theme_paytable_meters() -> None:
    from network.accounting_state_loader import aggregate_theme_paytable_meters

    totals = aggregate_theme_paytable_meters(
        {
            "return_94_0": {"coinin": "100", "gamesplayed": "2"},
            "return_92_0": {"coinin": "70", "gamesplayed": "1"},
        }
    )
    assert totals == {"coinin": "170", "gamesplayed": "3"}


def test_build_coin_panel_display_rows_aggregate() -> None:
    from network.sas_serial_meters import build_coin_panel_display_rows, catalog_coin_panel_rows

    rows, totals = build_coin_panel_display_rows("in", machine_state={"coinin": "940"})
    assert len(rows) == len(catalog_coin_panel_rows("in"))
    assert totals == {"amount_cents": 940, "count": 0, "sas_code": "0000"}


def test_probe_com_port_available_reports_missing_port() -> None:
    from network.sas_serial_meters import probe_com_port_available

    ok, msg = probe_com_port_available("COM99999")
    assert not ok
    assert "COM99999" in msg


def test_find_running_sas_com_blockers_returns_list() -> None:
    from network.sas_serial_meters import find_running_sas_com_blockers

    assert isinstance(find_running_sas_com_blockers(), list)


def test_sas_poll_keeper_probe_cli() -> None:
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(repo / "scripts" / "sas_poll_keeper.py"), "--probe", "COM99999"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1


def test_verify_coins_table_spec_ok() -> None:
    from gui.sas_verify_dialog import (
        _COINS_CATALOG_ROW_COUNT,
        _COINS_TABLE_COLUMN_COUNT,
        _COINS_TABLE_HEADERS,
        verify_coins_table_spec,
    )

    assert verify_coins_table_spec(
        column_count=_COINS_TABLE_COLUMN_COUNT,
        headers=_COINS_TABLE_HEADERS,
        vertical_header_hidden=True,
        row_count=_COINS_CATALOG_ROW_COUNT + 1,
    ) == []


def test_verify_accounting_tab_spec_ok() -> None:
    from gui.sas_verify_dialog import _METER_TAB_NAMES, verify_accounting_tab_spec
    from network.sas_serial_meters import DEFAULT_6F_VERIFY_POLL_CODES

    assert verify_accounting_tab_spec(
        tab_names=_METER_TAB_NAMES,
        has_accounting_panel=True,
        verify_row_count=len(DEFAULT_6F_VERIFY_POLL_CODES),
        expected_row_count=len(DEFAULT_6F_VERIFY_POLL_CODES),
    ) == []


def test_filter_verify_6f_rows_subset() -> None:
    from gui.sas_verify_dialog import (
        build_verify_6f_rows_from_paste,
        filter_verify_6f_rows,
    )
    from network.sas_serial_meters import IGT_TESTER_6F_POLL_BATCHES

    line = "RX<= 01 6F 00 01 00 00 00 02 00 34"
    all_rows = build_verify_6f_rows_from_paste(line)
    batch1 = filter_verify_6f_rows(all_rows, IGT_TESTER_6F_POLL_BATCHES[0])
    assert len(batch1) == 7
    assert next(r for r in batch1 if r.meter_id == "0000").sas_value_text == "34"


def test_accounting_tab_gui_layout_automated() -> None:
    """Headless Qt GUI test: Accounting Meters panel centered and columns compact."""
    from types import SimpleNamespace

    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import (
        COL_METER_NAME,
        COL_STATUS,
        SasVerifyDialog,
        TAB_ACCOUNTING,
        _METER_TAB_NAMES,
        _VERIFY_METER_NAME_MAX_WIDTH,
        build_verify_6f_rows_from_paste,
        verify_accounting_tab_layout,
        verify_stretch_widget_in_panel_layout,
    )
    from network.sas_serial_meters import DEFAULT_6F_VERIFY_POLL_CODES

    app = QApplication.instance() or QApplication([])
    vm = SimpleNamespace(current_product_name="GUI-Test")
    dlg = SasVerifyDialog(vm, QThreadPool.globalInstance(), scan_root="")
    dlg._prefetch_started = True  # avoid COM/cabinet prefetch side effects in CI
    dlg.resize(1180, 780)

    sample_paste = "\n".join(
        [
            "RX<= 01 6F 00 01 00 00 00 02 00 34",
            "RX<= 01 6F 00 01 00 05 00 02 00 82",
            "RX<= 01 6F 00 01 00 06 00 02 00 28",
        ]
    )
    dlg._paste.setPlainText(sample_paste)
    parsed = build_verify_6f_rows_from_paste(sample_paste)
    dlg._render(parsed_rows=parsed, allow_machine_lookup=False)
    app.processEvents()

    assert dlg._meter_tabs.currentIndex() == TAB_ACCOUNTING
    tab_layout = dlg._accounting_tab.layout()
    assert tab_layout is not None
    assert verify_stretch_widget_in_panel_layout(tab_layout) == []

    issues = verify_accounting_tab_layout(
        accounting_tab=dlg._accounting_tab,
        accounting_box=dlg._accounting_box,
        table=dlg._table,
        tab_names=_METER_TAB_NAMES,
    )
    assert issues == [], f"layout issues: {issues}"

    assert dlg._table.rowCount() == len(DEFAULT_6F_VERIFY_POLL_CODES)
    assert not dlg._table.isColumnHidden(COL_STATUS)
    assert dlg._table.columnWidth(COL_METER_NAME) <= _VERIFY_METER_NAME_MAX_WIDTH
    assert dlg._table.horizontalScrollBar().maximum() == 0
    status_item = dlg._table.item(0, COL_STATUS)
    assert status_item is not None
    assert status_item.text() in {"MATCH", "MISMATCH", "PENDING"}


def test_sas_verify_window_flags_include_system_menu() -> None:
    from PySide6.QtCore import Qt

    from gui.sas_verify_dialog import _SAS_VERIFY_WINDOW_FLAGS

    assert _SAS_VERIFY_WINDOW_FLAGS & Qt.WindowType.WindowSystemMenuHint
    assert _SAS_VERIFY_WINDOW_FLAGS & Qt.WindowType.WindowCloseButtonHint
