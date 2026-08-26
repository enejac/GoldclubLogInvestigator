"""Accounting-tab (first SasVerify page) share-only meter check."""

from __future__ import annotations

from network.sas_serial_meters import DEFAULT_6F_VERIFY_POLL_CODES
from automation.sas_accounting_page_check import (
    check_accounting_page,
    write_report,
)


def _full_state(**overrides: str) -> dict[str, str]:
    """Minimal keys covering the Accounting poll codes used in unit tests."""
    base = {
        "coinin": "100",
        "coinout": "50",
        "totaljackpot": "0",
        "cancelledcredits": "10",
        "gamesplayed": "5",
        "gameswon": "1",
        "notesinstackeramt": "20",
        "currentcredits": "1000",
        "vouchercashableinamt": "0",
        "vouchernoncashinamt": "0",
        "voucherpromoinamt": "0",
        "vouchercashableoutamt": "0",
        "vouchernoncashoutamt": "0",
        "voucherpromooutamt": "0",
        "watcashableinamt": "0",
        "watnoncashinamt": "0",
        "watpromoinamt": "0",
        "watcashableoutamt": "0",
        "watnoncashoutamt": "0",
        "watpromooutamt": "0",
        "totaldrop": "0",
        "physicalcoinin": "0",
        "physicalcoinout": "0",
        "truecoin": "0",
        "bonus": "0",
        "attendantpaid": "0",
        "machinepaid": "0",
    }
    base.update(overrides)
    return base


def test_default_codes_are_thirty() -> None:
    assert len(DEFAULT_6F_VERIFY_POLL_CODES) == 30


def test_all_codes_match_when_states_equal() -> None:
    state = _full_state()
    report = check_accounting_page(
        "10.0.0.90",
        scan_root=r"\\fake\\root",
        machine_state=state,
        sas_state=dict(state),
        treat_syncing_as_ok=True,
    )
    assert report.codes == 30
    assert report.match + report.not_reported + report.missing == 30
    assert report.mismatch == 0
    assert report.ok
    assert all(r.status in {"MATCH", "NOT REPORTED", "NO MACHINE"} for r in report.rows)


def test_unequal_is_syncing_by_default() -> None:
    machine = _full_state(coinin="100")
    sas = _full_state(coinin="105")
    report = check_accounting_page(
        "10.0.0.90",
        scan_root=r"\\fake",
        machine_state=machine,
        sas_state=sas,
        treat_syncing_as_ok=True,
    )
    by_code = {r.code: r for r in report.rows}
    assert by_code["0000"].status == "SYNCING"
    assert report.syncing >= 1
    assert report.ok


def test_strict_unequal_is_mismatch() -> None:
    machine = _full_state(coinin="100")
    sas = _full_state(coinin="999")
    report = check_accounting_page(
        "10.0.0.90",
        scan_root=r"\\fake",
        machine_state=machine,
        sas_state=sas,
        treat_syncing_as_ok=False,
    )
    by_code = {r.code: r for r in report.rows}
    assert by_code["0000"].status == "MISMATCH"
    assert report.mismatch >= 1
    assert not report.ok


def test_empty_machine_fails() -> None:
    report = check_accounting_page(
        "10.0.0.90",
        scan_root=r"\\fake",
        machine_state={},
        sas_state=_full_state(),
    )
    assert not report.ok
    assert report.missing == 30


def test_write_report_emits_json_and_md(tmp_path) -> None:
    state = _full_state()
    report = check_accounting_page(
        "10.0.0.90",
        scan_root=r"\\fake",
        machine_state=state,
        sas_state=dict(state),
    )
    path = write_report(report, tmp_path)
    assert path.is_file()
    assert (tmp_path / "accounting_page_latest.json").is_file()
    mds = list(tmp_path.glob("accounting_page_*.md"))
    assert mds
    text = mds[0].read_text(encoding="utf-8")
    assert "Accounting page check" in text
    assert "0000" in text
