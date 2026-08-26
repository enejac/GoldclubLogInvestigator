"""Post-RAM-clear / soft-meters: unify omitted SAS zeros with Machine $0."""

from __future__ import annotations

from gui.sas_verify_dialog import compare_status, missing_sas_status


def test_missing_sas_status_not_reported_when_capture_done() -> None:
    assert missing_sas_status(sas_capture_expected=False) == "NOT REPORTED"
    assert missing_sas_status(sas_capture_expected=True) == "PENDING"


def test_compare_status_match() -> None:
    assert compare_status(match=True, sources_settled=True) == "MATCH"


def _unify_row_status(*, has_sas: bool, sas_v: str, machine_v: str, capture_expected: bool) -> str:
    """Mirror the post-clear unify branch in SasVerifyDialog._render_table."""
    machine_missing = not bool(machine_v)
    sas_norm = ((sas_v or "").replace(".", "").lstrip("0") or "0") if has_sas else ""
    mac_norm = ((machine_v or "").replace(".", "").lstrip("0") or "0") if machine_v else ""
    if not has_sas:
        if (not machine_missing) and mac_norm == "0" and not capture_expected:
            return compare_status(match=True, sources_settled=True)
        return missing_sas_status(sas_capture_expected=capture_expected)
    if machine_missing:
        return "NO MACHINE"
    try:
        match = int(mac_norm) == int(sas_norm)
    except ValueError:
        match = mac_norm == sas_norm
    return compare_status(match=match, sources_settled=True)


def test_unify_omitted_sas_zero_machine_to_match() -> None:
    assert (
        _unify_row_status(
            has_sas=False, sas_v="", machine_v="0", capture_expected=False
        )
        == "MATCH"
    )


def test_unify_does_not_invent_while_capture_pending() -> None:
    assert (
        _unify_row_status(
            has_sas=False, sas_v="", machine_v="0", capture_expected=True
        )
        == "PENDING"
    )


def test_unify_leaves_nonzero_machine_as_not_reported() -> None:
    """Stacker / unpaid SAS still NOT REPORTED when Machine has a real amount."""
    assert (
        _unify_row_status(
            has_sas=False, sas_v="", machine_v="18800", capture_expected=False
        )
        == "NOT REPORTED"
    )
