"""
Indefinite Auto-fetch soak against a remote cabinet (default 10.0.0.90).

Drives SasVerifyDialog headlessly: prefetch + Auto fetch, then samples settle
health every few seconds. Ctrl+C to stop.

  python -m automation.sas_verify_autofetch_soak
  python -m automation.sas_verify_autofetch_soak --ip 10.0.0.90 --interval 5
  python -m automation.sas_verify_autofetch_soak --ip 10.0.0.90 --skip-com
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QApplication

from config import format_unc_log_root
from gui.sas_verify_dialog import COL_STATUS, SAS_STATUS_SYNCING, SasVerifyDialog
from network.lab_access import ensure_lab_smb_credential


def _scan_root_for_ip(ip: str) -> str:
    try:
        return format_unc_log_root(ip)
    except Exception:
        return rf"\\{ip}\c$\Goldclub\var\log"


def _count_status(dlg: SasVerifyDialog, wanted: str) -> int:
    n = 0
    for tbl in dlg._verify_tables:
        for row in range(tbl.rowCount()):
            item = tbl.item(row, COL_STATUS)
            if item is None:
                continue
            if (item.text() or "").strip().upper() == wanted.upper():
                n += 1
    return n


def _sample(dlg: SasVerifyDialog) -> dict:
    status = (dlg._prefetch_status_label.text() or "").strip()
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "scan_root": (dlg._scan_root_edit.text() or dlg._scan_root or "").strip(),
        "auto_fetch": bool(dlg._auto_fetch_toggle.isChecked()),
        "machine_loaded": bool(dlg._machine_loaded_for_current_root()),
        "machine_keys": len(dlg._machine_state or {}),
        "compare_running": bool(dlg._compare_running()),
        "meter_fetch_running": bool(dlg._meter_fetch_running()),
        "local_diff_running": bool(dlg._local_diff_running),
        "force_pending": bool(dlg._cabinet_compare_force_pending),
        "round_active": bool(dlg._auto_fetch_round_active),
        "round_resynced": bool(dlg._auto_fetch_round_resynced),
        "sources_settled": bool(dlg._compare_sources_settled()),
        "syncing_rows": _count_status(dlg, SAS_STATUS_SYNCING),
        "no_machine_rows": _count_status(dlg, "NO MACHINE"),
        "status": status,
        "stuck_loading": "Loading Machine meters" in status,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ip", default="10.0.0.90", help="Cabinet IP (UNC Machine + COM)")
    p.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between health samples (default 5)",
    )
    p.add_argument(
        "--out",
        default="",
        help="JSONL output path (default _tmp_logs/sas_autofetch_soak/<stamp>.jsonl)",
    )
    p.add_argument(
        "--skip-com",
        action="store_true",
        help="Only exercise Machine Auto-fetch (skip local COM — useful when COM4 is busy)",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Seconds to run then exit (0 = until Ctrl+C)",
    )
    p.add_argument(
        "--max-bad",
        type=int,
        default=0,
        help="Exit non-zero after this many BAD samples (0 = never fail on BAD)",
    )
    args = p.parse_args(argv)

    ip = (args.ip or "").strip()
    ensure_lab_smb_credential(ip)
    scan_root = _scan_root_for_ip(ip)

    out = Path(args.out) if args.out else Path("_tmp_logs") / "sas_autofetch_soak" / (
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{ip}_autofetch.jsonl"
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)
    vm = SimpleNamespace(
        current_product_name="AutofetchSoak",
        get_gm2u_value_for_sas_code=lambda code, state: "",
    )
    dlg = SasVerifyDialog(vm, QThreadPool.globalInstance(), scan_root=scan_root)
    dlg._scan_root_edit.setText(scan_root)
    dlg._scan_root = scan_root
    if args.skip_com:
        dlg._begin_meter_fetch = lambda **kw: False  # type: ignore[method-assign]
        dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]
    # Defaults are already on; show so timers/prefetch arm like a real session.
    dlg.show()
    app.processEvents()

    print(
        f"soak start ip={ip} scan_root={scan_root} skip_com={args.skip_com} out={out}",
        flush=True,
    )
    print("Ctrl+C to stop.", flush=True)

    ticks = {"n": 0, "ok": 0, "bad": 0}

    def tick() -> None:
        sample = _sample(dlg)
        ticks["n"] += 1
        bad = (
            sample["stuck_loading"]
            or sample["no_machine_rows"] > 0
            or (
                sample["machine_keys"] == 0
                and not sample["compare_running"]
                and not sample["local_diff_running"]
                and ticks["n"] > 3
            )
        )
        if bad:
            ticks["bad"] += 1
            tag = "BAD"
        else:
            ticks["ok"] += 1
            tag = "ok"
        line = json.dumps(sample, ensure_ascii=False)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(
            f"[{tag}] n={ticks['n']} ok={ticks['ok']} bad={ticks['bad']} "
            f"keys={sample['machine_keys']} settled={sample['sources_settled']} "
            f"syncing={sample['syncing_rows']} no_machine={sample['no_machine_rows']} "
            f"round={sample['round_active']} | {sample['status'][:90]}",
            flush=True,
        )
        if args.max_bad and ticks["bad"] >= args.max_bad:
            print(
                f"soak abort max-bad={args.max_bad} n={ticks['n']} out={out}",
                flush=True,
            )
            dlg.close()
            app.quit()

    timer = QTimer()
    timer.setInterval(max(1000, int(args.interval * 1000)))
    timer.timeout.connect(tick)
    QTimer.singleShot(1500, tick)
    timer.start()

    if args.duration and args.duration > 0:
        def _stop() -> None:
            print(
                f"soak stop duration={args.duration}s n={ticks['n']} "
                f"ok={ticks['ok']} bad={ticks['bad']} out={out}",
                flush=True,
            )
            dlg.close()
            app.quit()

        QTimer.singleShot(int(args.duration * 1000), _stop)

    try:
        code = app.exec()
    except KeyboardInterrupt:
        print(
            f"soak stop n={ticks['n']} ok={ticks['ok']} bad={ticks['bad']} out={out}",
            flush=True,
        )
        return 0
    if args.max_bad and ticks["bad"] >= args.max_bad:
        return 1
    return int(code) if code else 0


if __name__ == "__main__":
    raise SystemExit(main())
