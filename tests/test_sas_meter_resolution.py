"""SAS 6F -> gm2u machine-value resolution (view_model.get_gm2u_value_for_sas_code).

These tests use synthetic ``machine_state`` dicts whose keys match the *real*
normalized key names emitted by ``network.accounting_state_loader`` (verified
against the lab cabinet at 10.0.0.90), e.g. ``watcashableinamt`` from a
deviceClass="wat"/meterName="cashableInAmt" pair.
"""

from __future__ import annotations

import re

from gui.view_model import (
    IncidentViewModel,
    SAS_6F_METER_ALIASES,
    SAS_6F_TO_IGT_2F_INDEX,
    base_sas_6f_meter_code,
    format_sas_6f_meter_code_display,
    igt_meter_display_id,
    sas_6f_code_for_igt_2f_index,
    sas_6f_meter_column_values,
    igt_2f_meter_index_for_6f_code,
    sas_6f_wire_code,
)


def _make_vm() -> IncidentViewModel:
    # Bypass QObject/__init__: get_gm2u_value_for_sas_code is pure Python and
    # only uses ``self`` to recurse, so no Qt event loop is required here.
    return IncidentViewModel.__new__(IncidentViewModel)


def _norm_compare(value: str | None) -> str:
    """Mirror SasVerifyDialog._normalize_int_for_compare for the integer case.

    The dialog drops any decimal point (gm2u meters are integer units that the
    resolver may present scaled as "x.yy") and strips leading zeros.
    """
    s = (value or "").strip()
    if "." in s:
        s = s.replace(".", "")
    if not re.fullmatch(r"\d+", s):
        return s
    return s.lstrip("0") or "0"


def _cabinet_state() -> dict[str, str]:
    """Synthetic state with the real normalized keys the loader produces."""
    return {
        # 0003 HandPaidCancelled shared bucket.
        "cancelledcredits": "2531786",
        # 0016 TotalTicketOut (voucher cashable out).
        "vouchercashableoutamt": "100227650",
        # Ticket/voucher in buckets (0015 aggregate + per-bucket 0080/0082/0084).
        "vouchercashableinamt": "8340",
        "vouchernoncashinamt": "0",
        "voucherpromoinamt": "0",
        # Ticket/voucher out bucket (per-bucket 0088).
        "vouchernoncashoutamt": "0",
        # WAT in buckets (0017 aggregate + per-bucket A0/A2/A4).
        "watcashableinamt": "850000",
        "watnoncashinamt": "10000",
        "watpromoinamt": "2000",
        # WAT out buckets (0018 aggregate + per-bucket B8/BA/BC).
        "watcashableoutamt": "10000",
        "watnoncashoutamt": "8000",
        "watpromooutamt": "0",
        # 000B bills-in (note acceptor stacker total).
        "notesinstackeramt": "1000",
    }


def test_0004_is_handpaid_plus_ticketout_plus_cashlessout() -> None:
    vm = _make_vm()
    state = _cabinet_state()
    # 2531786 + 100227650 + 18000 = 102777436
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0004", state)) == "102777436"


def test_cashless_in_buckets_resolve() -> None:
    vm = _make_vm()
    state = _cabinet_state()
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("00A0", state)) == "850000"
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("00A2", state)) == "10000"
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("00A4", state)) == "2000"


def test_cashless_out_buckets_resolve() -> None:
    vm = _make_vm()
    state = _cabinet_state()
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("00B8", state)) == "10000"
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("00BA", state)) == "8000"
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("00BC", state)) == "0"


def test_aggregate_wat_rows_still_match() -> None:
    vm = _make_vm()
    state = _cabinet_state()
    # 0017 = 850000 + 10000 + 2000 ; 0018 = 10000 + 8000 + 0
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0017", state)) == "862000"
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0018", state)) == "18000"


def test_ticket_voucher_buckets_resolve() -> None:
    vm = _make_vm()
    state = _cabinet_state()
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0080", state)) == "8340"
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0082", state)) == "0"
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0084", state)) == "0"
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0086", state)) == "100227650"
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0088", state)) == "0"


def test_ticket_aliases_start_with_display_names() -> None:
    for code in ("0080", "0082", "0084", "0086", "0088"):
        assert " " in SAS_6F_METER_ALIASES[code][0]


def test_000b_bills_in_resolves_from_notes_in_stacker() -> None:
    vm = _make_vm()
    state = _cabinet_state()
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("000B", state)) == "1000"


def test_meter_code_display_includes_igt_2f_hint() -> None:
    # Lab-verified: IGT index 22 -> meter 0200 (Total Jackpot / display 0002).
    assert format_sas_6f_meter_code_display("0002") == "0002 · 0200 ($2F ; 22)"
    assert base_sas_6f_meter_code("0002 · 0200 ($2F ; 22)") == "0002"
    assert igt_2f_meter_index_for_6f_code("0002") == "22"
    assert sas_6f_wire_code("0002") == "0200"
    assert igt_meter_display_id("0002") == "00000200"
    assert sas_6f_meter_column_values("0002") == ("0002", "0200", "22", "00000200")
    # Every alias row used in verify has an IGT index; display wraps it.
    for code, igt_ix in SAS_6F_TO_IGT_2F_INDEX.items():
        assert code in SAS_6F_METER_ALIASES
        wire = sas_6f_wire_code(code)
        wire_part = f" · {wire}" if wire else ""
        assert format_sas_6f_meter_code_display(code) == f"{code}{wire_part} ($2F ; {igt_ix})"
        assert sas_6f_meter_column_values(code)[0] == code
        assert sas_6f_meter_column_values(code)[1] == wire
        assert sas_6f_meter_column_values(code)[2] == igt_ix
        assert sas_6f_meter_column_values(code)[3] == wire.zfill(8)


def test_0004_components_resolve_individually() -> None:
    vm = _make_vm()
    state = _cabinet_state()
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0003", state)) == "2531786"
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0016", state)) == "100227650"
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0018", state)) == "18000"


def test_dollar_display_converts_sas_credits() -> None:
    from gui.sas_verify_dialog import EgmCurrency, _format_meter_value_display

    c = EgmCurrency()
    assert _format_meter_value_display("100", meter_code="0000", currency=c, show_dollars=True) == "$1"
    assert _format_meter_value_display("12600", meter_code="0002", currency=c, show_dollars=True) == "$126"


def test_dollar_display_machine_values_already_scaled() -> None:
    from gui.sas_verify_dialog import EgmCurrency, _format_meter_value_display

    c = EgmCurrency()
    assert _format_meter_value_display("126.00", meter_code="0000", currency=c, show_dollars=True) == "$126"


def test_dollar_display_skips_game_counters() -> None:
    from gui.sas_verify_dialog import EgmCurrency, _format_meter_value_display

    c = EgmCurrency()
    assert _format_meter_value_display("90673", meter_code="0005", currency=c, show_dollars=True) == "90673"


def test_credits_mode_shows_integer_not_gm2u_decimal() -> None:
    from gui.sas_verify_dialog import EgmCurrency, _format_meter_value_display

    c = EgmCurrency()
    assert _format_meter_value_display("1.00", meter_code="0000", currency=c, show_dollars=False) == "100"
    assert _format_meter_value_display("101.00", meter_code="0000", currency=c, show_dollars=False) == "10100"
    assert _format_meter_value_display("101.00", meter_code="0000", currency=c, show_dollars=True) == "$101"


def test_parse_sas_2f_paste_maps_index_to_6f_code() -> None:
    from gui.sas_verify_dialog import parse_sas_2f_paste

    assert sas_6f_code_for_igt_2f_index("52") == "0005"
    assert sas_6f_code_for_igt_2f_index("02") == "0000"
    assert sas_6f_code_for_igt_2f_index("00") == ""

    paste = """
    RX<= 01 2F 07 00 00 52 00 00 00 00 D2 B2
    RX<= 01 2F 07 00 00 62 00 00 00 00 03 66
    RX<= 01 2F 02 00 00 56 83
    RX<= 01 2F 07 00 00 02 00 00 00 00 B0 C7
    RX<= 01 2F 07 00 00 12 00 00 00 00 F0 73
    RX<= 01 2F 07 00 00 00 00 00 01 00 E0 C8
    """
    got = parse_sas_2f_paste(paste)
    assert got["0005"] == "0"
    assert got["0006"] == "0"
    assert got["0000"] == "0"
    assert got["0001"] == "0"
    assert "0007" not in got  # truncated 72 response ignored


def test_parse_sas_2f_igt_first_meter_text() -> None:
    from gui.sas_verify_dialog import parse_sas_2f_paste

    paste = """
    TX>= 01 2F 03 00 00 02 99 08
    $2F = First Meter             = 00000100
    TX>= 01 2F 03 00 00 52 1C 5A
    RX<= 01 2F 07 00 00 52 00 00 00 01 D2 B2
    """
    got = parse_sas_2f_paste(paste)
    assert got["0000"] == "100"
    assert got["0005"] == "1"


def test_default_verify_column_visible() -> None:
    from gui.sas_verify_dialog import (
        COL_6F_CODE,
        COL_IGT_METER,
        COL_IGT_POLL,
        COL_METER_NAME,
        COL_MACHINE_VALUE,
        COL_SAS_2F_VALUE,
        COL_SAS_6F_VALUE,
        COL_STATUS,
        COL_WIRE_ID,
        default_verify_column_visible,
    )

    assert default_verify_column_visible(COL_6F_CODE) is True
    assert default_verify_column_visible(COL_WIRE_ID) is False
    assert default_verify_column_visible(COL_IGT_POLL) is True
    assert default_verify_column_visible(COL_IGT_METER) is True
    assert default_verify_column_visible(COL_METER_NAME) is True
    assert default_verify_column_visible(COL_SAS_6F_VALUE) is True
    assert default_verify_column_visible(COL_SAS_2F_VALUE) is False
    assert default_verify_column_visible(COL_MACHINE_VALUE) is True
    assert default_verify_column_visible(COL_STATUS) is True


def test_visible_verify_table_headers_subset() -> None:
    from gui.sas_verify_dialog import (
        COL_6F_CODE,
        COL_COUNT,
        COL_METER_NAME,
        COL_SAS_6F_VALUE,
        visible_verify_table_headers,
    )

    column_visible = {col: False for col in range(COL_COUNT)}
    column_visible[COL_6F_CODE] = True
    column_visible[COL_METER_NAME] = True
    column_visible[COL_SAS_6F_VALUE] = True
    headers = visible_verify_table_headers(
        show_dollars=False,
        currency_symbol="$",
        column_visible=column_visible,
    )
    assert headers == ["6F Code", "Meter Name", "SAS (6F)"]


def test_normalize_int_for_compare_empty_is_blank() -> None:
    from gui.sas_verify_dialog import SasVerifyDialog

    dlg = SasVerifyDialog.__new__(SasVerifyDialog)
    assert dlg._normalize_int_for_compare("") == ""
    assert dlg._normalize_int_for_compare("600") == "600"
    assert dlg._normalize_int_for_compare("0.00") == "0"


def test_0006_defaults_zero_when_games_played() -> None:
    vm = _make_vm()
    state = {"gamesplayed": "1"}
    assert vm.get_gm2u_value_for_sas_code("0006", state) == "0"


def test_0007_games_lost_played_minus_won() -> None:
    vm = _make_vm()
    state = {"gamesplayed": "1"}
    assert vm.get_gm2u_value_for_sas_code("0007", state) == "1"


def test_wat_bucket_defaults_zero_when_wat_active() -> None:
    vm = _make_vm()
    state = {"watcashableinamt": "500"}
    assert vm.get_gm2u_value_for_sas_code("00A2", state) == "0"
    assert vm.get_gm2u_value_for_sas_code("00B8", state) == "0"
    assert vm.get_gm2u_value_for_sas_code("0018", state) == "0"


def test_001c_does_not_reuse_0001_coin_out_aggregate() -> None:
    vm = _make_vm()
    state = {
        "bggamecoinout": "0",
        "sasbonuswin": "500",
        "progwin": "500",
    }
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0001", state)) == "1000"
    assert vm.get_gm2u_value_for_sas_code("001C", state) is None


def test_001c_uses_coinout_when_present() -> None:
    vm = _make_vm()
    state = {"coinout": "250000"}
    assert vm.get_gm2u_value_for_sas_code("001C", state) == "250000"


def test_0001_coin_out_includes_sas_and_prog_wins() -> None:
    vm = _make_vm()
    state = {
        "coinout": "140",
        "basegamecoinout": "140",
        "bggamecoinout": "140",
        "sasbonuswin": "500",
        "progwin": "500",
    }
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0001", state)) == "1140"


def test_0001_coin_out_sums_theme_win_meters() -> None:
    vm = _make_vm()
    state = {
        "bggamecoinout": "0",
        "sasbonuswin": "500",
        "progwin": "500",
    }
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0001", state)) == "1000"


def test_0001_coin_out_from_lab_cabinet_state() -> None:
    """Live-cabinet check: 0001 = max(paytable coin-out keys) + sasbonuswin + progwin.

    Expectation is computed from the loaded state (cabinet meters move with play),
    not hardcoded.
    """
    from network.accounting_state_loader import load_machine_accounting_state_pure

    vm = _make_vm()
    state = load_machine_accounting_state_pure(r"\\\\10.0.0.90\\c\$\\Goldclub\\var\\log")
    if not state:
        return

    def _i(key: str) -> int:
        try:
            return int(str(state.get(key, "0")).strip() or "0")
        except ValueError:
            return 0

    paytable = max(
        _i(k)
        for k in (
            "coinout",
            "totalcoinout",
            "gamecoinout",
            "basegamecoinout",
            "bggamecoinout",
            "scattercoinout",
            "progscattercoinout",
            "addscattercoinout",
        )
    )
    expected = paytable + _i("sasbonuswin") + _i("progwin")
    assert _norm_compare(vm.get_gm2u_value_for_sas_code("0001", state)) == str(expected)


def test_cabinet_bill_aggregate_keys_from_lab_state() -> None:
    from network.accounting_state_loader import (
        cabinet_bill_reject_count,
        cabinet_bill_stacker_amount_credits,
        cabinet_bill_stacker_count,
    )

    state = {
        "notesinstackeramt": "600",
        "notesinstackercnt": "2",
        "billrejectcnt": "0",
    }
    assert cabinet_bill_stacker_amount_credits(state) == "600"
    assert cabinet_bill_stacker_count(state) == "2"
    assert cabinet_bill_reject_count(state) == "0"
