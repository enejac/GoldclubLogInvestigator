"""OneHand.MainFrm / SlotMachine version extraction."""

from parser import extract_onehand_from_lines, parse_log_file
from pathlib import Path


def test_extract_onehand_standard_info_line() -> None:
    line = (
        "2026-03-15T00:00:00.551+00:00  INFO  [SlotMachine] "
        "OneHand.MainFrm - SlotMachine v2.14.0 — startup"
    )
    assert extract_onehand_from_lines([line]) == "2.14.0"


def test_extract_onehand_extra_whitespace() -> None:
    line = "2026-03-12 14:57:08  INFO  [SlotMachine]  OneHand.MainFrm  -  SlotMachine  v1.0.3"
    assert extract_onehand_from_lines([line]) == "1.0.3"


def test_extract_onehand_last_wins_in_batch() -> None:
    lines = [
        "INFO OneHand.MainFrm - SlotMachine v1.0.0",
        "INFO OneHand.MainFrm - SlotMachine v1.0.1",
    ]
    assert extract_onehand_from_lines(lines) == "1.0.1"


def test_parse_log_file_includes_onehand(tmp_path: Path) -> None:
    p = tmp_path / "slot.log"
    p.write_text(
        "2026-03-15T00:00:00.551+00:00  INFO  [SlotMachine] "
        "OneHand.MainFrm - SlotMachine v9.8.7\n",
        encoding="utf-8",
    )
    r = parse_log_file(p)
    assert r.onehand_version == "9.8.7"
