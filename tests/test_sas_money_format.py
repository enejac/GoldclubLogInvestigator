"""SAS verify dollar/credit formatting."""

from __future__ import annotations

from gui.sas_money_format import (
    EgmCurrency,
    credits_to_dollar_amount,
    currency_from_id,
    detect_egm_currency,
    format_dollar_amount,
    format_meter_value_display,
    is_monetary_sas_code,
)


def test_credits_to_dollars() -> None:
    assert credits_to_dollar_amount("100") == 1.0
    assert credits_to_dollar_amount("126.00") == 126.0
    assert credits_to_dollar_amount("0") == 0.0


def test_format_dollar_amount() -> None:
    assert format_dollar_amount(1.0, symbol="$") == "$1"
    assert format_dollar_amount(1.26, symbol="$") == "$1.26"


def test_format_meter_value_display_toggle() -> None:
    cur = EgmCurrency(symbol="$", code="USD")
    assert format_meter_value_display("100", meter_code="0001", currency=cur, show_dollars=False) == "100"
    assert format_meter_value_display("100", meter_code="0001", currency=cur, show_dollars=True) == "$1"
    assert format_meter_value_display("100", meter_code="0005", currency=cur, show_dollars=True) == "100"


def test_is_monetary_sas_code() -> None:
    assert is_monetary_sas_code("0001")
    assert not is_monetary_sas_code("0005")


def test_detect_egm_currency_default() -> None:
    assert detect_egm_currency("").symbol == "$"


def test_currency_from_id_known_codes() -> None:
    eur = currency_from_id("EUR")
    assert eur is not None and eur.symbol == "\u20ac" and eur.code == "EUR"
    usd = currency_from_id("usd")
    assert usd is not None and usd.symbol == "$" and usd.code == "USD"
    cop = currency_from_id("COP")
    assert cop is not None and cop.symbol == "$" and cop.code == "COP"


def test_currency_from_id_unknown_iso_shows_code() -> None:
    cur = currency_from_id("XYZ")
    assert cur is not None and cur.code == "XYZ" and "XYZ" in cur.symbol


def test_currency_from_id_empty_or_garbage_is_none() -> None:
    assert currency_from_id("") is None
    assert currency_from_id("   ") is None
    assert currency_from_id("12") is None


def test_currency_keeps_credit_scale() -> None:
    eur = currency_from_id("EUR")
    assert eur is not None
    assert format_meter_value_display(
        "100", meter_code="0001", currency=eur, show_dollars=True
    ) == "\u20ac1"


def test_state_xml_currency_id_extraction(tmp_path) -> None:
    from network.accounting_state_loader import flatten_xml_file_to_norm_map

    xml = tmp_path / "DeviceManagerData.xml_1"
    xml.write_text(
        """<?xml version="1.0"?>
<deviceManager xmlns:d4p1="http://x">
  <d4p1:curMeter d4p1:meterName="curInAmt" d4p1:currencyId="USD" d4p1:denomId="500"
      d4p1:currencyType="note" d4p1:meterValue="10"/>
  <processorStatus d4p1:currencyId="EUR" d4p1:localeId="sl_SI"/>
  <d4p1:perfMeter d4p1:meterName="coinIn" d4p1:meterValue="1234"/>
</deviceManager>
""",
        encoding="utf-8",
    )
    flat = flatten_xml_file_to_norm_map(xml)
    # processorStatus is authoritative over per-meter currencyId.
    assert flat.get("__currencyid__") == "EUR"
    assert flat.get("coinin") == "1234"


def test_state_xml_currency_id_from_curmeter_only(tmp_path) -> None:
    from network.accounting_state_loader import flatten_xml_file_to_norm_map

    xml = tmp_path / "DeviceManagerData.xml_1"
    xml.write_text(
        """<?xml version="1.0"?>
<deviceManager xmlns:d4p1="http://x">
  <d4p1:curMeter d4p1:meterName="curInAmt" d4p1:currencyId="COP" d4p1:denomId="500"
      d4p1:currencyType="note" d4p1:meterValue="10"/>
</deviceManager>
""",
        encoding="utf-8",
    )
    flat = flatten_xml_file_to_norm_map(xml)
    assert flat.get("__currencyid__") == "COP"


def test_dialog_defaults_show_money_and_adopts_state_currency() -> None:
    """Headless Qt: Show $ starts checked; EUR from cabinet state relabels toggle/headers."""
    from types import SimpleNamespace

    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from gui.sas_verify_dialog import SasVerifyDialog

    app = QApplication.instance() or QApplication([])
    vm = SimpleNamespace(current_product_name="GUI-Test")
    dlg = SasVerifyDialog(vm, QThreadPool.globalInstance(), scan_root="")
    dlg._prefetch_started = True  # avoid COM/cabinet prefetch side effects in CI

    assert dlg._show_dollars is True
    assert dlg._dollar_toggle.isChecked()
    assert dlg._dollar_toggle.text() == "Show $"

    # Currency arrives with cabinet state; reserved key must not leak into meters.
    dlg._apply_cabinet_state({"coinin": "1234", "__currencyid__": "EUR"})
    app.processEvents()
    assert "__currencyid__" not in dlg._machine_state
    assert dlg._machine_state.get("coinin") == "1234"
    assert dlg._currency.code == "EUR"
    assert dlg._dollar_toggle.text() == "Show \u20ac"
    assert "\u20ac" in dlg._value_header_labels()[0]

    # Unknown/absent id keeps the resolved currency.
    dlg._apply_state_currency("")
    assert dlg._currency.code == "EUR"
    dlg.deleteLater()
    app.processEvents()


def test_state_xml_no_currency_id_has_no_reserved_key(tmp_path) -> None:
    from network.accounting_state_loader import flatten_xml_file_to_norm_map

    xml = tmp_path / "DeviceManagerData.xml_1"
    xml.write_text(
        """<?xml version="1.0"?>
<deviceManager xmlns:d4p1="http://x">
  <d4p1:perfMeter d4p1:meterName="coinIn" d4p1:meterValue="7"/>
</deviceManager>
""",
        encoding="utf-8",
    )
    flat = flatten_xml_file_to_norm_map(xml)
    assert "__currencyid__" not in flat
