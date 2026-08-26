"""Verify every meter on SasVerify first page (Accounting tab).

Accounting tab = 30 codes in DEFAULT_6F_VERIFY_POLL_CODES.
Compares Machine (gm2au) vs SASControler1 over SMB ? safe while
SasVerifyMeters.exe holds COM (no second COM poll).

  python -m automation.sas_accounting_page_check --ip 10.0.0.90
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gui.sas_verify_dialog import SAS_STATUS_NOT_REPORTED, compare_status
from gui.view_model import IncidentViewModel, SAS_6F_METER_ALIASES
from network.accounting_state_loader import (
    load_machine_accounting_state_pure,
    load_sas_controller_state,
)
from network.sas_serial_meters import DEFAULT_6F_VERIFY_POLL_CODES

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class MeterRow:
    code: str
    name: str
    machine: str | None
    sas: str | None
    status: str


@dataclass
class AccountingPageReport:
    ok: bool
    ip: str
    scan_root: str
    mode: str
    codes: int
    match: int
    mismatch: int
    syncing: int
    missing: int
    not_reported: int
    rows: list[MeterRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ts_utc: str = ""


def _norm_num(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Mirror dialog integer compare: drop decimal point, strip leading zeros.
    if "." in s:
        s = s.replace(".", "")
    if s.isdigit():
        return str(int(s))
    return s


def _name_for_code(code: str) -> str:
    aliases = SAS_6F_METER_ALIASES.get(code.upper(), [])
    return str(aliases[0]) if aliases else code


def scan_root_for_ip(ip: str) -> str:
    try:
        from config import format_unc_log_root

        return format_unc_log_root(ip)
    except Exception:
        return rf"\\{ip}\c$\Goldclub\var\log"


def check_accounting_page(
    ip: str,
    *,
    scan_root: str | None = None,
    codes: tuple[str, ...] = DEFAULT_6F_VERIFY_POLL_CODES,
    treat_syncing_as_ok: bool = True,
    machine_state: dict[str, str] | None = None,
    sas_state: dict[str, str] | None = None,
) -> AccountingPageReport:
    """Compare all Accounting-tab meters: gm2au (Machine) vs SASControler1 (SAS).

    When both sides are present but differ, status is SYNCING (play lag) unless
    ``treat_syncing_as_ok`` is False, in which case unequal values are MISMATCH.
    """
    root = (scan_root or scan_root_for_ip(ip)).strip()
    notes: list[str] = []
    try:
        from network.lab_access import ensure_lab_smb_credential

        ensure_lab_smb_credential(ip)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"smb_cred_warn: {exc}")

    machine = machine_state if machine_state is not None else (
        load_machine_accounting_state_pure(root) or {}
    )
    sas = sas_state if sas_state is not None else (load_sas_controller_state(root) or {})
    if not machine:
        notes.append("machine_state empty (gm2au DeviceManager)")
    if not sas:
        notes.append("sas_controller_state empty (SASControler1 snapshot)")

    vm = IncidentViewModel.__new__(IncidentViewModel)
    rows: list[MeterRow] = []
    match = mismatch = syncing = missing = not_reported = 0

    for code in codes:
        c = str(code).strip().upper()
        # Derived meters (e.g. 0004) can resolve to "0" on an empty dict;
        # treat a truly empty Machine snapshot as unloaded.
        if not machine:
            mac = None
            sas_v = _norm_num(vm.get_gm2u_value_for_sas_code(c, sas)) if sas else None
        else:
            mac = _norm_num(vm.get_gm2u_value_for_sas_code(c, machine))
            sas_v = _norm_num(vm.get_gm2u_value_for_sas_code(c, sas)) if sas else None

        if mac is None and sas_v is None:
            status = "NO MACHINE"
            missing += 1
        elif mac is None:
            status = "NO MACHINE"
            missing += 1
        elif sas_v is None:
            # Share-only: no COM ? absent SAS side is NOT REPORTED (same as GUI).
            status = SAS_STATUS_NOT_REPORTED
            not_reported += 1
        elif mac == sas_v:
            status = "MATCH"
            match += 1
        elif treat_syncing_as_ok:
            status = compare_status(match=False, sources_settled=False)
            syncing += 1
        else:
            status = compare_status(match=False, sources_settled=True)
            mismatch += 1

        rows.append(
            MeterRow(
                code=c,
                name=_name_for_code(c),
                machine=mac,
                sas=sas_v,
                status=status,
            )
        )

    hard_fail = mismatch > 0 or (not machine)
    # Soft: SYNCING / NOT REPORTED during play are expected on a live EGM.
    ok = (not hard_fail) and bool(machine) and (match + syncing) > 0

    return AccountingPageReport(
        ok=ok,
        ip=ip,
        scan_root=root,
        mode="share_gm2au_vs_sascontroller",
        codes=len(codes),
        match=match,
        mismatch=mismatch,
        syncing=syncing,
        missing=missing,
        not_reported=not_reported,
        rows=rows,
        notes=notes,
        ts_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def write_report(report: AccountingPageReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"accounting_page_{stamp}.json"
    payload = asdict(report)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md = out_dir / f"accounting_page_{stamp}.md"
    lines = [
        f"# Accounting page check ({report.ip})",
        "",
        f"- ok: **{report.ok}**",
        f"- mode: `{report.mode}`",
        (
            f"- MATCH {report.match} / SYNCING {report.syncing} / "
            f"MISMATCH {report.mismatch} / NOT REPORTED {report.not_reported} / "
            f"missing {report.missing} (of {report.codes})"
        ),
        f"- scan_root: `{report.scan_root}`",
        f"- ts: {report.ts_utc}",
        "",
        "| Code | Name | Machine | SAS | Status |",
        "|------|------|---------|-----|--------|",
    ]
    for r in report.rows:
        lines.append(
            f"| {r.code} | {r.name} | {r.machine} | {r.sas} | {r.status} |"
        )
    if report.notes:
        lines.extend(["", "## Notes", *[f"- {n}" for n in report.notes]])
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "accounting_page_latest.json").write_text(
        path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return path


def run_accounting_page_check(
    ip: str,
    *,
    out_dir: Path | str | None = None,
    treat_syncing_as_ok: bool = True,
    scan_root: str | None = None,
) -> dict[str, Any]:
    """Library entry for soak / bots. Returns a summary dict + report path."""
    report = check_accounting_page(
        ip,
        scan_root=scan_root,
        treat_syncing_as_ok=treat_syncing_as_ok,
    )
    dest = Path(out_dir) if out_dir else (ROOT / "_tmp_logs" / "sas_accounting_page_check")
    path = write_report(report, dest)
    summary = {k: v for k, v in asdict(report).items() if k != "rows"}
    summary["path"] = str(path)
    summary["rows"] = [asdict(r) for r in report.rows]
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--scan-root", default="")
    p.add_argument("--out-dir", default="")
    p.add_argument(
        "--strict-syncing",
        action="store_true",
        help="Treat unequal Machine/SAS values as MISMATCH (fail) instead of SYNCING",
    )
    args = p.parse_args(argv)
    out = Path(args.out_dir) if args.out_dir else (
        ROOT / "_tmp_logs" / "sas_accounting_page_check"
    )
    summary = run_accounting_page_check(
        args.ip,
        out_dir=out,
        treat_syncing_as_ok=not args.strict_syncing,
        scan_root=args.scan_root or None,
    )
    printable = {k: v for k, v in summary.items() if k != "rows"}
    print(json.dumps(printable, indent=2))
    print(
        "rows",
        summary["match"],
        "MATCH",
        summary["syncing"],
        "SYNCING",
        summary["mismatch"],
        "MISMATCH",
        summary["not_reported"],
        "NOT_REPORTED",
    )
    print(f"wrote {summary['path']}", flush=True)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
