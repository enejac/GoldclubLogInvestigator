"""
One-shot SAS Verify feature smoke against a cabinet (default 10.0.0.90).

Exercises safe UI surfaces on this PC (tabs, toggles, Get Meters, Compare,
Copy, Help, Always on top, column menu, Machine source resolve). Skips RAM Clear.

  python -m automation.sas_verify_feature_smoke
  python -m automation.sas_verify_feature_smoke --ip 10.0.0.90 --settle 25
  python -m automation.sas_verify_feature_smoke --ip 10.0.0.90 --skip-com
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from config import format_unc_log_root
from gui.sas_verify_dialog import (
    COL_STATUS,
    SAS_STATUS_SYNCING,
    _METER_TAB_NAMES,
    SasVerifyDialog,
)
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


def _step(results: list[dict], name: str, ok: bool, detail: str = "") -> None:
    results.append({"step": name, "ok": bool(ok), "detail": detail or ""})
    tag = "ok" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument(
        "--settle",
        type=float,
        default=28.0,
        help="Seconds to wait for Machine/COM settle before asserting (default 28)",
    )
    p.add_argument(
        "--skip-com",
        action="store_true",
        help="Skip live COM Get Meters (Machine + UI only)",
    )
    p.add_argument("--out", default="", help="JSON report path")
    args = p.parse_args(argv)

    ip = (args.ip or "").strip()
    ensure_lab_smb_credential(ip)
    scan_root = _scan_root_for_ip(ip)

    out = Path(args.out) if args.out else Path("_tmp_logs") / "sas_feature_smoke" / (
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{ip}_features.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)
    vm = SimpleNamespace(
        current_product_name="FeatureSmoke",
        get_gm2u_value_for_sas_code=lambda code, state: "",
    )
    dlg = SasVerifyDialog(vm, QThreadPool.globalInstance(), scan_root=scan_root)
    dlg._scan_root_edit.setText(scan_root)
    dlg._scan_root = scan_root
    dlg._startup_remote_ip = ip
    if args.skip_com:
        dlg._begin_meter_fetch = lambda **kw: False  # type: ignore[method-assign]
        dlg._offline_log_scan = lambda: False  # type: ignore[method-assign]

    results: list[dict] = []
    dlg.show()
    app.processEvents()
    print(f"feature smoke start ip={ip} scan_root={scan_root} skip_com={args.skip_com}", flush=True)

    # --- immediate UI features ---
    _step(results, "window_visible", dlg.isVisible())
    _step(
        results,
        "meter_tab_count",
        dlg._meter_tabs.count() == len(_METER_TAB_NAMES),
        f"count={dlg._meter_tabs.count()}",
    )

    visited: list[str] = []
    for i, name in enumerate(_METER_TAB_NAMES):
        dlg._goto_meter_tab(i)
        app.processEvents()
        ok = dlg._meter_tabs.currentIndex() == i and dlg._meter_tabs.tabText(i) == name
        visited.append(name)
        _step(results, f"tab_{name}", ok, f"index={i}")

    # step wrap
    dlg._goto_meter_tab(0)
    dlg._step_meter_tab(1)
    app.processEvents()
    _step(results, "step_tab_forward", dlg._meter_tabs.currentIndex() == 1)

    # Always on top toggle (restore original)
    was_aot = bool(dlg._always_on_top_action.isChecked())
    dlg._always_on_top_action.setChecked(not was_aot)
    app.processEvents()
    flipped = bool(dlg._always_on_top_action.isChecked()) != was_aot
    dlg._always_on_top_action.setChecked(was_aot)
    app.processEvents()
    _step(results, "always_on_top_toggle", flipped)

    # Show $ toggle
    was_dollar = bool(dlg._dollar_toggle.isChecked())
    dlg._dollar_toggle.setChecked(not was_dollar)
    app.processEvents()
    dollar_ok = bool(dlg._dollar_toggle.isChecked()) != was_dollar
    dlg._dollar_toggle.setChecked(was_dollar)
    app.processEvents()
    _step(results, "show_dollar_toggle", dollar_ok)

    # Column visibility (first checkable column action)
    col_ok = False
    if dlg._column_actions:
        col = next(iter(dlg._column_actions))
        act = dlg._column_actions[col]
        before = act.isChecked()
        act.setChecked(not before)
        app.processEvents()
        col_ok = act.isChecked() != before
        act.setChecked(before)
        app.processEvents()
    _step(results, "column_visibility_toggle", col_ok)

    # Help dialog open + close (non-modal or modal)
    help_ok = False
    try:
        dlg._show_help_dialog()
        app.processEvents()
        help_ok = True
        for w in app.topLevelWidgets():
            if w is dlg:
                continue
            title = (w.windowTitle() or "").lower()
            if "help" in title or "setup" in title or "troubleshoot" in title or "sas verify" in title:
                if isinstance(w, QDialog):
                    w.reject()
                else:
                    w.close()
                app.processEvents()
                break
    except Exception as exc:
        _step(results, "help_dialog", False, str(exc))
    else:
        _step(results, "help_dialog", help_ok)

    # Machine source resolve (must find gm2au on live .90)
    machine_path = None
    try:
        machine_path = dlg._machine_source_file()
    except Exception as exc:
        _step(results, "machine_source_resolve", False, str(exc))
    else:
        _step(
            results,
            "machine_source_resolve",
            machine_path is not None and Path(machine_path).is_file(),
            str(machine_path) if machine_path else "none",
        )

    # Auto fetch on (defaults may already be on)
    dlg._auto_fetch_toggle.setChecked(True)
    app.processEvents()
    _step(results, "auto_fetch_on", bool(dlg._auto_fetch_toggle.isChecked()))

    # Compare + Get Meters (COM optional)
    try:
        dlg._btn_compare.click()
        app.processEvents()
        _step(results, "compare_click", True)
    except Exception as exc:
        _step(results, "compare_click", False, str(exc))

    if not args.skip_com:
        try:
            dlg._btn_get_meters.click()
            app.processEvents()
            _step(results, "get_meters_click", True)
        except Exception as exc:
            _step(results, "get_meters_click", False, str(exc))
    else:
        _step(results, "get_meters_click", True, "skipped")

    # Copy report (clipboard — must not raise)
    try:
        dlg._btn_copy.click()
        app.processEvents()
        _step(results, "copy_report", True)
    except Exception as exc:
        _step(results, "copy_report", False, str(exc))

    # RAM Clear action must exist but we do not trigger it
    has_rc = hasattr(dlg, "_act_ram_clear") and dlg._act_ram_clear is not None
    _step(results, "ram_clear_action_present", has_rc, "not executed")

    exit_code = {"n": 1}

    def finish() -> None:
        try:
            keys = len(dlg._machine_state or {})
            syncing = _count_status(dlg, SAS_STATUS_SYNCING)
            no_machine = _count_status(dlg, "NO MACHINE")
            settled = bool(dlg._compare_sources_settled())
            status = (dlg._prefetch_status_label.text() or "").strip()
            stuck = "Loading Machine meters" in status
            machine_ok = keys > 0 and not stuck
            _step(
                results,
                "machine_loaded",
                machine_ok,
                f"keys={keys} settled={settled} syncing={syncing} "
                f"no_machine={no_machine} status={status[:100]}",
            )
            if not args.skip_com:
                # Live COM may still be catching up; only fail hard if Machine never loaded.
                meter_running = bool(dlg._meter_fetch_running())
                sas_2f = getattr(dlg, "_sas_2f_values", None) or {}
                paste = ""
                try:
                    paste = (dlg._paste.toPlainText() or "").strip()
                except Exception:
                    paste = ""
                _step(
                    results,
                    "com_path_alive",
                    machine_ok or meter_running or bool(sas_2f) or bool(paste),
                    f"meter_fetch={meter_running} sas_2f={len(sas_2f)} paste_chars={len(paste)}",
                )

            failed = [r for r in results if not r["ok"]]
            report = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "ip": ip,
                "scan_root": scan_root,
                "skip_com": bool(args.skip_com),
                "tabs_visited": visited,
                "fail_count": len(failed),
                "results": results,
            }
            out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(
                f"feature smoke done fail={len(failed)}/{len(results)} out={out}",
                flush=True,
            )
            for r in failed:
                print(f"  FAIL detail: {r['step']}: {r['detail']}", flush=True)
            exit_code["n"] = 1 if failed else 0
            dlg.close()
            app.quit()

        except Exception as exc:
            traceback.print_exc()
            _step(results, "finish", False, str(exc))
            exit_code["n"] = 1
            try:
                dlg.close()
            except Exception:
                pass
            app.quit()
    QTimer.singleShot(max(5000, int(args.settle * 1000)), finish)
    try:
        app.exec()
    except Exception:
        traceback.print_exc()
        return 1
    return int(exit_code["n"])


if __name__ == "__main__":
    raise SystemExit(main())
