from __future__ import annotations

from pathlib import Path

from automation.theme_loader import parse_math_settings


def test_parse_math_settings_extracts_bet_steps_and_lines() -> None:
    xml_text = Path("tests/fixtures/mathsettings_bigwin.xml").read_text(
        encoding="utf-8", errors="strict"
    )
    cfg = parse_math_settings(xml_text, theme_id="PR3_BigWinHD")
    assert cfg.theme_id == "PR3_BigWinHD"
    assert cfg.number_of_lines == 30
    assert cfg.denom_multiplier == 1
    assert cfg.bet_multipliers == (1, 2, 3, 4, 5, 8, 10, 12, 15)
    assert cfg.math_file and cfg.math_file.endswith(".thm")

