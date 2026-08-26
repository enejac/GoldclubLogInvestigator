"""Deep inventory of Ruleta LLAVE / trial token files on a lab cabinet.

Captures file presence, size, mtime, SHA1, and whether RouletteActivate is all-zero.
Use before and after manual LLAVE entry to see exactly what changed.

Example:
  python scripts/scan_trial_token_state.py --host 10.0.0.111 --label error99_before
  python scripts/scan_trial_token_state.py --host 10.0.0.111 --label error99_after
  python scripts/scan_trial_token_state.py --compare error99_before error99_after
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

OUT_DIR = _REPO / "_tmp_logs" / "trial_token_scan"

# Canonical trial paths (see roulette_trial.py + Fix-Error30Clock.ps1).
CANONICAL_REL: tuple[str, ...] = (
    "ruleta/persistent/RouletteActivate.dat",
    "ruleta/persistent/RouletteStop.flag",
    "ruleta/persistent/HeapDataFinanceStamps.dat",
    "ruleta/persistent/HeapDataDateTime.dat",
    "ruleta/var/Password.dat",
    "ruleta/var/HeapDataDateTime.dat",
    "var/state/error30.password",
    "var/state/gci-backup-trial",
)

PERSISTENT_KEEP: tuple[str, ...] = (
    "ruleta/persistent/HeapDataTitoPowerUp.dat",
    "ruleta/persistent/HeapDataWatTransactions.dat",
    "ruleta/persistent/DynamicPaytableWheelCurrent.dat",
    "ruleta/persistent/HeapDataLongLongWrapper.dat",
)

LICENCE_PATHS: tuple[str, ...] = (
    "config/licences/37A55022DCBEF351AE27471D181B1EF5.xml",
    "ruleta/licence.dll",
)

SEARCH_NAMES: tuple[str, ...] = (
    "RouletteActivate.dat",
    "RouletteStop.flag",
    "HeapDataFinanceStamps.dat",
    "Password.dat",
    "HeapDataDateTime.dat",
    "error30.password",
)


def _sha1(path: Path) -> str | None:
    try:
        h = hashlib.sha1()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _file_record(root: Path, rel: str) -> dict:
    path = root / Path(rel.replace("/", "\\"))
    rec: dict = {"rel": rel.replace("\\", "/"), "exists": False}
    try:
        if not path.is_file():
            return rec
        data = path.read_bytes()
        rec["exists"] = True
        rec["size"] = len(data)
        rec["sha1"] = _sha1(path)
        rec["mtimeUtc"] = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        rec["all_zeros"] = not any(data)
        if rel.endswith("RouletteActivate.dat") and len(data) >= 8:
            rec["activate_bound"] = not rec["all_zeros"]
        if rel.endswith("HeapDataFinanceStamps.dat") and len(data) >= 8:
            import struct

            stamp = struct.unpack_from("<I", data, 4)[0]
            if 1_500_000_000 <= stamp <= 2_200_000_000:
                rec["finance_stamp_unix"] = stamp
                rec["finance_stamp_iso"] = datetime.fromtimestamp(
                    stamp, tz=timezone.utc
                ).isoformat()
    except OSError as exc:
        rec["error"] = str(exc)
    return rec


def _dir_listing(root: Path, rel_dir: str, *, max_files: int = 200) -> list[dict]:
    base = root / Path(rel_dir.replace("/", "\\"))
    out: list[dict] = []
    if not base.is_dir():
        return out
    try:
        files = sorted(base.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return out
    for path in files[:max_files]:
        if not path.is_file():
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        out.append(_file_record(root, rel))
    return out


def _search_by_name(root: Path) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    for name in SEARCH_NAMES:
        try:
            hits = list(root.rglob(name))
        except OSError:
            continue
        for path in hits:
            try:
                rel = str(path.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            if rel in seen:
                continue
            seen.add(rel)
            found.append(_file_record(root, rel))
    return sorted(found, key=lambda r: r.get("rel", ""))


def _log_trial_excerpt(root: Path, *, tail_lines: int = 80) -> dict:
    from roulette_trial import find_latest_trial_displayed, trial_log_state

    log_root = root / "var" / "log"
    out: dict = {
        "trial_log_state": trial_log_state(root),
        "latest_displayed": None,
        "recent_trial_lines": [],
    }
    ch = find_latest_trial_displayed(root)
    if ch is not None:
        out["latest_displayed"] = {
            "error_code": ch.error_code,
            "ui_system_id": ch.ui_system_id,
            "challenge_raw": ch.challenge_raw,
        }
    for folder in log_root.iterdir() if log_root.is_dir() else []:
        if "ruleta" not in folder.name.casefold():
            continue
        try:
            logs = sorted(folder.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            continue
        if not logs:
            continue
        try:
            text = logs[0].read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        trial_lines = [
            ln for ln in lines[-tail_lines:] if "TRIAL" in ln or "Trial expired" in ln
        ]
        out["recent_trial_lines"] = trial_lines[-15:]
        out["newest_log"] = str(logs[0].relative_to(root)).replace("\\", "/")
        break
    return out


def scan_cabinet(host: str) -> dict:
    from network.lab_access import ensure_lab_smb_credential

    ensure_lab_smb_credential(host)
    roots: list[tuple[str, Path]] = []
    for unc in (rf"\\{host}\slot", rf"\\{host}\c$\Goldclub", rf"\\{host}\c$\goldclub"):
        try:
            p = Path(unc)
            if p.is_dir():
                roots.append((unc, p))
        except OSError:
            continue
    if not roots:
        raise FileNotFoundError(f"Cannot reach GoldClub on {host} (slot or c$)")

    primary_label, primary = roots[0]
    report: dict = {
        "host": host,
        "scannedAtUtc": datetime.now(timezone.utc).isoformat(),
        "primaryRoot": primary_label,
        "accessibleRoots": [label for label, _ in roots],
        "canonical": [_file_record(primary, rel) for rel in CANONICAL_REL if not rel.endswith("/")],
        "persistentKeep": [_file_record(primary, rel) for rel in PERSISTENT_KEEP],
        "licence": [_file_record(primary, rel) for rel in LICENCE_PATHS],
        "gciBackupTrial": _dir_listing(primary, "var/state/gci-backup-trial"),
        "searchHits": _search_by_name(primary),
        "logs": _log_trial_excerpt(primary),
        "buildVersion": None,
    }

    bv = primary / "ruleta" / "BuildVersion.txt"
    if bv.is_file():
        try:
            report["buildVersion"] = bv.read_text(encoding="utf-8", errors="replace")[:500]
        except OSError:
            pass

    # D: USB / ConfigScanner backup locations (may differ from slot junction).
    extra_dirs = (
        rf"\\{host}\D$\ConfigScanner\backup-persistent-trial-clear30",
        rf"\\{host}\D$\ConfigScanner\backup-persistent-trial-20260820",
        rf"\\{host}\D$\usb_scripts\roulette\error30.password",
        rf"\\{host}\USB\usb_scripts\roulette\error30.password",
    )
    report["extraPaths"] = []
    for unc in extra_dirs:
        p = Path(unc)
        try:
            if p.is_file():
                rel = unc.split("$", 1)[-1] if "$" in unc else unc
                report["extraPaths"].append({"unc": unc, **_file_record(p.parent, p.name)})
            elif p.is_dir():
                items = []
                for child in sorted(p.iterdir())[:50]:
                    if child.is_file():
                        items.append(
                            {
                                "name": child.name,
                                "size": child.stat().st_size,
                                "sha1": _sha1(child),
                            }
                        )
                report["extraPaths"].append({"unc": unc, "files": items})
        except OSError:
            report["extraPaths"].append({"unc": unc, "exists": False})

    # WinRM: clock + processes (live cabinet view).
    try:
        from automation.remote_exec import winrm_run_inline
        from network.lab_access import require_lab_fleet_ip

        ip = require_lab_fleet_ip(host)
        ps = r"""
        $out = @()
        $out += "clock=" + (Get-Date -Format o)
        $w = Get-Service w32time -ErrorAction SilentlyContinue
        if ($w) { $out += ("w32time=" + $w.Status) }
        foreach ($n in @('Ruleta','ruleta','godot')) {
          $p = @(Get-Process -Name $n -ErrorAction SilentlyContinue)
          if ($p) { $out += ($n + "_count=" + $p.Count) }
        }
        $out -join "`n"
        """
        r = winrm_run_inline(ip=ip, script=ps.strip(), timeout=45)
        report["winrmLive"] = ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as exc:
        report["winrmLive"] = f"unavailable: {exc}"

    return report


def _compare(before: dict, after: dict) -> dict:
    def by_rel(items: list[dict]) -> dict[str, dict]:
        return {i["rel"]: i for i in items if i.get("rel")}

    b_can = by_rel(before.get("canonical", []))
    a_can = by_rel(after.get("canonical", []))
    changed: list[dict] = []
    for rel in sorted(set(b_can) | set(a_can)):
        old, new = b_can.get(rel, {}), a_can.get(rel, {})
        if old.get("sha1") != new.get("sha1") or old.get("exists") != new.get("exists"):
            changed.append({"rel": rel, "before": old, "after": new})

    b_search = {i["rel"]: i for i in before.get("searchHits", [])}
    a_search = {i["rel"]: i for i in after.get("searchHits", [])}
    search_changed: list[dict] = []
    for rel in sorted(set(b_search) | set(a_search)):
        old, new = b_search.get(rel, {}), a_search.get(rel, {})
        if old.get("sha1") != new.get("sha1") or old.get("exists") != new.get("exists"):
            search_changed.append({"rel": rel, "before": old, "after": new})

    return {
        "canonicalChanged": changed,
        "searchChanged": search_changed,
        "logState": {
            "before": before.get("logs", {}).get("trial_log_state"),
            "after": after.get("logs", {}).get("trial_log_state"),
        },
        "displayed": {
            "before": before.get("logs", {}).get("latest_displayed"),
            "after": after.get("logs", {}).get("latest_displayed"),
        },
    }


def _print_summary(report: dict) -> None:
    print(f"host={report.get('host')} scanned={report.get('scannedAtUtc')}")
    print(f"root={report.get('primaryRoot')}")
    disp = report.get("logs", {}).get("latest_displayed")
    if disp:
        print(f"ERROR {disp.get('error_code')} system_id={disp.get('ui_system_id')}")
    print(f"log_state={report.get('logs', {}).get('trial_log_state')}")
    print("\n--- canonical trial files ---")
    for rec in report.get("canonical", []):
        if not rec.get("exists"):
            print(f"  {rec['rel']}: MISSING")
            continue
        flags = []
        if rec.get("all_zeros"):
            flags.append("all_zeros")
        if rec.get("activate_bound"):
            flags.append("BOUND")
        if rec.get("finance_stamp_iso"):
            flags.append(f"stamp={rec['finance_stamp_iso']}")
        extra = f" [{', '.join(flags)}]" if flags else ""
        print(f"  {rec['rel']}: {rec.get('size')}b sha1={rec.get('sha1', '')[:12]}…{extra}")
    print("\n--- search hits (all RouletteActivate / Password / error30 on disk) ---")
    for rec in report.get("searchHits", []):
        z = " zeros" if rec.get("all_zeros") else ""
        print(f"  {rec['rel']}: {rec.get('size', 0)}b{z}")
    gci = report.get("gciBackupTrial") or []
    if gci:
        print(f"\n--- gci-backup-trial ({len(gci)} files sampled) ---")
        for rec in gci[:12]:
            z = " zeros" if rec.get("all_zeros") else " NONZERO"
            print(f"  {rec['rel']}: {rec.get('size', 0)}b{z}")
    if report.get("winrmLive"):
        print(f"\n--- live (WinRM) ---\n{report['winrmLive']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan Ruleta trial token state on cabinet")
    parser.add_argument("--host", default="10.0.0.111")
    parser.add_argument("--label", default="", help="Save report as _tmp_logs/trial_token_scan/<label>.json")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.compare:
        before_path = OUT_DIR / f"{args.compare[0]}.json"
        after_path = OUT_DIR / f"{args.compare[1]}.json"
        before = json.loads(before_path.read_text(encoding="utf-8"))
        after = json.loads(after_path.read_text(encoding="utf-8"))
        diff = _compare(before, after)
        diff_path = OUT_DIR / f"diff_{args.compare[0]}__{args.compare[1]}.json"
        diff_path.write_text(json.dumps(diff, indent=2), encoding="utf-8")
        print(json.dumps(diff, indent=2))
        print(f"\nWrote {diff_path}")
        return 0

    report = scan_cabinet(args.host.strip())
    label = (args.label or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")).strip()
    out_path = OUT_DIR / f"{label}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _print_summary(report)
    print(f"\nFull report: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
