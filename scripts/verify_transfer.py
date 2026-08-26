"""Verify cabinet state after autonomous version transfer."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from network.ruleta_stack_probe import probe_blocking_processes, verify_stack_running


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--host", default="10.0.0.111")
    p.add_argument("--expect-version", required=True)
    args = p.parse_args()

    from automation.remote_exec import winrm_run_inline
    from network.lab_access import ensure_lab_smb_credential, require_lab_fleet_ip

    host = require_lab_fleet_ip(args.host)
    ensure_lab_smb_credential(host)

    script = r"""
    $v = (Get-ItemProperty 'C:\Goldclub\ruleta\Ruleta.exe' -ErrorAction Stop).VersionInfo.ProductVersion
    $act = Test-Path 'C:\goldclub\ruleta\persistent\RouletteActivate.dat'
    $fin = Test-Path 'C:\goldclub\ruleta\persistent\HeapDataFinanceStamps.dat'
    "VERSION=$v ACTIVATE=$act FINANCE=$fin"
    """
    r = winrm_run_inline(ip=host, script=script.strip(), timeout=45)
    blob = ((r.stdout or "") + (r.stderr or "")).strip()
    print("cabinet:", blob)

    expect = args.expect_version.strip()
    ok_ver = expect in blob
    print("version_ok:", ok_ver, f"(expected {expect})")

    running = probe_blocking_processes(host)
    print("processes:", ",".join(running) if running else "none")

    up, detail = verify_stack_running(host, require_godot=True)
    print("stack_ok:", up, detail)

    log_script = r"""
    $log = Get-ChildItem 'C:\Goldclub\var\log\ruleta Roulette\*.log' -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $log) { 'NO_LOG'; exit 0 }
    $tail = Get-Content -LiteralPath $log.FullName -Tail 40
    $patterns = 'error="30"','Trial expired','error="99"','Bets are open','SUCCEEDED'
    foreach ($pat in $patterns) {
      $hits = @($tail | Select-String -SimpleMatch $pat)
      if ($hits) { "HIT $pat=$($hits.Count)" }
    }
    """
    lr = winrm_run_inline(ip=host, script=log_script.strip(), timeout=60)
    lblob = ((lr.stdout or "") + (lr.stderr or "")).strip()
    print("log_hits:")
    print(lblob or "(none)")

    if not ok_ver:
        return 1
    if not up:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
