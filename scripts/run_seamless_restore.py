"""One-shot FULL_SOFTWARE restore to a lab cabinet (Kill-All -> push -> config -> start)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/run_seamless_restore.py` from repo root.
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import traceback
from datetime import datetime, timezone

from config_scanner.service import ConfigScannerService
from config_scanner.stack_restart import plan_stack_restart, run_stack_kill, run_stack_start
from config_scanner.write_scope import WriteScope
from config_scanner.cabinet_trial_prep import (
    needs_seamless_trial_prep,
    prepare_cabinet_for_seamless_transfer,
)
from config_scanner.paytable_compat import live_ruleta_major_minor
from config_scanner.software_compat import snapshot_ruleta_major_minor
from config_scanner.build_version import scan_target_path
from config_scanner.scanner import load_build_info


def _log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seamless config + software restore")
    parser.add_argument("snapshot", help="Snapshot folder name under snapshots/")
    parser.add_argument(
        "--target",
        default=r"\\10.0.0.111\slot",
        help="Scan target UNC (default: \\\\10.0.0.111\\slot)",
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Skip Run-FullStack after restore",
    )
    parser.add_argument(
        "--skip-kill",
        action="store_true",
        help="Skip Kill-All (stack already stopped)",
    )
    parser.add_argument(
        "--no-llave",
        action="store_true",
        help="Do not auto-type a saved trial password after start",
    )
    parser.add_argument(
        "--clock",
        default="",
        help="Lab clock for Fix-Error30Clock (date only, e.g. 2026-08-10)",
    )
    args = parser.parse_args()

    target = args.target.strip()
    snapshot = args.snapshot.strip()
    scope = WriteScope.FULL_SOFTWARE.value

    svc = ConfigScannerService()
    _log(f"target={target} snapshot={snapshot} scope={scope}")

    refuses = svc.snapshot_apply_refuses(snapshot, target, write_scope=scope)
    if refuses:
        for item in refuses:
            _log(f"REFUSE: {item}")
        return 2

    plan = plan_stack_restart(target)
    if plan:
        _log(f"stack plan mode={plan.mode} host={plan.host}")
    else:
        _log("WARN: no stack restart plan (Kill-All / Run-FullStack scripts missing?)")

    snap_dir = svc.root / "snapshots" / snapshot
    build_info = load_build_info(snap_dir) if snap_dir.is_dir() else None
    dest_root = scan_target_path(target)
    live_mm = live_ruleta_major_minor(dest_root)
    snap_mm = snapshot_ruleta_major_minor(build_info) if build_info else None
    version_transfer = bool(
        live_mm and snap_mm and live_mm.strip() != snap_mm.strip()
    )
    from roulette_trial import snapshot_should_auto_enter_llave

    auto_llave = (not args.no_llave) and snapshot_should_auto_enter_llave(snap_dir)

    if plan and not args.skip_kill:
        if needs_seamless_trial_prep(
            target,
            write_scope=scope,
            version_transfer=version_transfer,
        ):
            serial = (build_info.machine_serial if build_info else None) or None
            _log("Trial prep (clock + clear persistent) …")
            prep_kw: dict = {
                "machine_serial": serial,
                "tool_root": svc.root,
                "sync_password": auto_llave,
            }
            if args.clock.strip():
                prep_kw["clock"] = args.clock.strip()
            ok, detail = prepare_cabinet_for_seamless_transfer(
                target,
                **prep_kw,
            )
            _log(("OK" if ok else "WARN") + f": {detail}")
            if not ok:
                _log("Continuing — push also clears trial tokens")
        _log("Kill-All …")
        ok, detail = run_stack_kill(plan)
        _log(("OK" if ok else "FAIL") + f": {detail}")
        if not ok:
            return 3

    _log("Presave live config (+ software) …")
    presave = svc.run_scan(target, include_software=True)
    _log(f"presave={presave.snapshot_name}")

    svc.capture_rollback_trial_bind(presave.snapshot_name, target)

    _log("Apply snapshot (software pack + config) …")
    try:
        result = svc.apply_snapshot_to_target(
            snapshot,
            target,
            write_scope=scope,
        )
    except Exception as exc:
        _log(f"FAIL apply: {exc}")
        traceback.print_exc()
        return 4

    _log(
        f"apply ok written={result.written_count} skipped={result.skipped_count} "
        f"errors={len(result.errors)} verify_ok={result.verify_ok}"
    )
    if result.errors:
        for err in result.errors[:5]:
            _log(f"  error: {err}")

    svc.record_rollback_snapshot(
        snapshot_name=presave.snapshot_name,
        restored_from=snapshot,
        write_scope=scope,
        scan_target=target,
    )

    if plan and not args.no_start:
        _log("Run-FullStack …")
        serial = None
        dest_root = scan_target_path(target)
        if version_transfer:
            from roulette_trial import (
                clear_stale_llave_after_software_swap,
                snapshot_has_bound_activate,
            )

            # Apply already put the snapshot's 876/10.1 token back. Wiping
            # again is what forced ERROR 99 on every 10.1↔10.2 swap.
            if snapshot_has_bound_activate(snap_dir):
                _log("Keep snapshot LLAVE bind (no post-apply wipe)")
            else:
                cleared = clear_stale_llave_after_software_swap(dest_root)
                _log(
                    f"Post-apply trial wipe ({len(cleared)} file(s)) "
                    "before stack start"
                )
        try:
            if build_info:
                serial = build_info.machine_serial
        except OSError:
            pass
        ok, detail = run_stack_start(
            plan,
            scan_target=target,
            ensure_llave=auto_llave,
            machine_serial=serial,
            tool_root=svc.root,
        )
        _log(("OK" if ok else "FAIL") + f": {detail}")
        if not ok:
            return 5

    _log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
