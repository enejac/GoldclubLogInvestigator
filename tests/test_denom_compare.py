"""Denom catalog vs 1-cent lock — Config Scanner compare policy."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from config_scanner.denom_compare import (
    classify_denom_changes,
    is_expected_rate_lock,
    parse_denom_list,
)
from config_scanner.report import (
    format_setting_change_description,
    is_actionable_content_change,
)
from config_scanner.xml_diff import (
    ContentChange,
    apply_xml_value_at_path,
    file_content_diff,
    flat_xml_map,
    is_catalog_content_change,
)

_FACTORY_CATALOG = "1,2,5,10,20,25,50,100,200,250,500,1000,1500,2000,50000"
_LIVE_CATALOG = "1,2,5,10,20,25,50,100,200,250,500,1000,1500,2000,5000"


def _mgconfig(denom_list: str, credit_rates: str) -> str:
    denom_ints = "".join(f"<int>{part}</int>" for part in denom_list.split(","))
    rate_ints = "".join(f"<int>{part}</int>" for part in credit_rates.split(","))
    return (
        '<?xml version="1.0"?>\n'
        "<Multigamer>\n"
        f"  <DenominationList>{denom_ints}</DenominationList>\n"
        f"  <CreditRateValues>{rate_ints}</CreditRateValues>\n"
        "  <ShowDenominationSelector>false</ShowDenominationSelector>\n"
        "</Multigamer>\n"
    )


def test_flat_xml_map_joins_denom_list_not_last_int_wins() -> None:
    tree = ET.ElementTree(ET.fromstring(_mgconfig(_LIVE_CATALOG, "1")))
    mapped = flat_xml_map(tree)
    assert mapped["Multigamer/DenominationList"] == _LIVE_CATALOG
    assert mapped["Multigamer/CreditRateValues"] == "1"
    assert "Multigamer/DenominationList/int" not in mapped


def test_factory_50000_vs_live_5000_is_catalog_not_red() -> None:
    changes = classify_denom_changes(
        [
            ContentChange(
                "modified",
                "Multigamer/DenominationList",
                _FACTORY_CATALOG,
                _LIVE_CATALOG,
            )
        ]
    )
    assert len(changes) == 1
    assert is_catalog_content_change(changes[0])
    assert not is_actionable_content_change(changes[0], relative_path="themes/mgconfig.xml")
    text = format_setting_change_description(changes[0])
    assert "catalog" in text
    assert "50000" in text
    assert "1 cent" in text


def test_credit_rate_factory_menu_to_1_cent_is_expected_lock() -> None:
    factory_rates = "1,2,5,10,20,25,50,100,200,250,500"
    assert is_expected_rate_lock(parse_denom_list(factory_rates), parse_denom_list("1"))
    changes = classify_denom_changes(
        [
            ContentChange(
                "modified",
                "Multigamer/CreditRateValues",
                factory_rates,
                "1",
            )
        ]
    )
    assert is_catalog_content_change(changes[0])


def test_credit_rate_1_vs_5_stays_modified() -> None:
    changes = classify_denom_changes(
        [
            ContentChange(
                "modified",
                "Multigamer/CreditRateValues",
                "1",
                "5",
            )
        ]
    )
    assert changes[0].change_type == "modified"
    assert is_actionable_content_change(changes[0], relative_path="themes/mgconfig.xml")


def test_file_content_diff_joins_and_classifies(tmp_path: Path) -> None:
    baseline = tmp_path / "default.xml"
    live = tmp_path / "live.xml"
    baseline.write_text(_mgconfig(_FACTORY_CATALOG, "1,2,5,10,20,25,50"), encoding="utf-8")
    live.write_text(_mgconfig(_LIVE_CATALOG, "1"), encoding="utf-8")
    changes = file_content_diff("themes/mgconfig.xml", baseline, live)
    kinds = {change.path.rsplit("/", 1)[-1]: change.change_type for change in changes}
    assert kinds["DenominationList"] == "catalog"
    assert kinds["CreditRateValues"] == "catalog"
    assert "int" not in kinds


def test_apply_rewrites_credit_rate_list(tmp_path: Path) -> None:
    path = tmp_path / "mgconfig.xml"
    path.write_text(_mgconfig(_LIVE_CATALOG, "1,2,5"), encoding="utf-8")
    apply_xml_value_at_path(path, "Multigamer/CreditRateValues", "1")
    mapped = flat_xml_map(ET.parse(path))
    assert mapped["Multigamer/CreditRateValues"] == "1"
    assert mapped["Multigamer/DenominationList"] == _LIVE_CATALOG
