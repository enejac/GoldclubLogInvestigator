"""Standalone launcher for SAS accounting / meters verification."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox, QWidget

from config import DEFAULT_REMOTE_IP
from config_manager import SettingsManager
from gui.app_branding import apply_app_icon
from gui.app_logging import configure_app_logging, get_logger
from gui.theme_utils import apply_theme
from gui.win_title_bar import install_title_bar_theme_filter, schedule_title_bar_theme
from network.goldclub_paths import extract_ip_from_path, format_unc_log_root

SAS_VERIFY_LOG_NAME = "SasVerifyMeters.log"
# Named mutex / shared-memory key. Bump if a stuck lock ever blocks launch.
SAS_VERIFY_SINGLE_INSTANCE_KEY = "GoldClub.SasVerifyMeters.v2"


class SasVerifyRuntime:
    """Minimal stand-in for IncidentViewModel — avoids loading the full LI graph.

    Still exposes the meter-resolution methods the Accounting table needs
    (``get_gm2u_value_for_sas_code`` / emergency SlotLog lookup / alias table).
    ``gui.view_model`` is imported on first meter lookup so window chrome can
    paint before that heavy module loads.
    """

    def __init__(self) -> None:
        from PySide6.QtCore import QThreadPool

        self._pool = QThreadPool.globalInstance()
        self.current_product_name: str | None = "SAS Verify Meters"
        self._meter_vm = None
        self._aliases: dict | None = None

    def _ensure_meter_vm(self):
        if self._meter_vm is None:
            from gui.view_model import IncidentViewModel, SAS_6F_METER_ALIASES

            # Bare instance: get_gm2u_* is pure Python and does not need QObject init.
            vm = IncidentViewModel.__new__(IncidentViewModel)
            vm._accounting_registers_cache = {}
            self._meter_vm = vm
            self._aliases = SAS_6F_METER_ALIASES
        return self._meter_vm

    @property
    def SAS_6F_METER_ALIASES(self):
        self._ensure_meter_vm()
        return self._aliases

    def thread_pool(self):
        return self._pool

    def get_gm2u_value_for_sas_code(
        self, sas_code: str, machine_state: dict[str, str]
    ) -> str | None:
        return self._ensure_meter_vm().get_gm2u_value_for_sas_code(sas_code, machine_state)

    def emergency_lookup_value_for_sas_code_from_logs(
        self, scan_root: str, sas_code: str
    ) -> str:
        return self._ensure_meter_vm().emergency_lookup_value_for_sas_code_from_logs(
            scan_root, sas_code
        )

    def invalidate_accounting_registers_cache(self, log_root: str | None = None) -> None:
        self._ensure_meter_vm().invalidate_accounting_registers_cache(log_root)



class _WinMutexLock:
    """Keeps a Win32 mutex handle open for the process lifetime."""

    def __init__(self, handle: int) -> None:
        self._handle = handle

    def detach(self) -> None:
        if not self._handle:
            return
        import ctypes

        ctypes.windll.kernel32.CloseHandle(self._handle)
        self._handle = 0

    def __del__(self) -> None:
        try:
            self.detach()
        except Exception:
            pass


def _try_acquire_win_mutex(name: str) -> object | None:
    """CreateMutex — OS releases it when the holding process dies (unlike QSharedMemory)."""
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return None
    # CreateMutex returns a handle even when another process already owns it.
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None
    return _WinMutexLock(int(handle))


def try_acquire_sas_verify_single_instance() -> object | None:
    """Return a process-lifetime lock, or None when another instance holds it.

    On Windows uses a named mutex so a crashed SasVerifyMeters does not leave a
    permanent "already running" warning (QSharedMemory orphans often do).

    Set ``SAS_VERIFY_ALLOW_MULTI=1`` to bypass (dev / automated UI tests only).
    """
    import os

    if os.environ.get("SAS_VERIFY_ALLOW_MULTI", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return object()

    if sys.platform == "win32":
        return _try_acquire_win_mutex(f"Local\\{SAS_VERIFY_SINGLE_INSTANCE_KEY}")

    from PySide6.QtCore import QSharedMemory

    key = SAS_VERIFY_SINGLE_INSTANCE_KEY
    mem = QSharedMemory(key)
    if mem.create(1):
        return mem
    if mem.attach():
        mem.detach()
    mem = QSharedMemory(key)
    if mem.create(1):
        return mem
    return None


def default_startup_scan_root(ip: str) -> str:
    """Default for the Scan root field: ``\\\\ip\\c$\\Goldclub\\var`` (no ``\\log``).

    Machine state (DeviceManager XML) lives under ``var`` — the state loader
    reads the same 61 keys from ``…\\var`` as from ``…\\var\\log``.
    """
    from pathlib import PureWindowsPath

    return str(PureWindowsPath(format_unc_log_root(ip)).parent)


def prefer_local_cabinet_scan_root_on_egm(
    scan_root: str,
    *,
    remote_ip: str | None = None,
    user_hint: bool = False,
) -> str:
    """On a cabinet, prefer ``C:\\Goldclub\\…`` over a persisted UNC to this machine."""
    root = (scan_root or "").strip()
    if user_hint or not root:
        return root
    from network.app_runtime import get_app_runtime, is_running_on_local_egm, is_this_host
    from network.goldclub_paths import (
        extract_ip_from_path,
        is_game_image_drive_path,
        normalize_path_str,
        resolve_sas_verify_standalone_scan_root,
    )

    if not is_running_on_local_egm():
        return root
    ip = (remote_ip or "").strip()
    if ip and not is_this_host(ip):
        return root
    normalized = normalize_path_str(root)
    if not normalized.startswith("\\\\"):
        return root
    from gui.sas_verify_dialog import _unc_host_only

    host = (extract_ip_from_path(normalized) or _unc_host_only(normalized) or "").strip()
    if not host or not is_this_host(host):
        return root
    ctx = get_app_runtime()
    local = (ctx.local_scan_root or "").strip()
    if local and not is_game_image_drive_path(local):
        return local
    discovery = resolve_sas_verify_standalone_scan_root(remote_ip=None)
    local = (discovery.scan_root or "").strip()
    if local and not is_game_image_drive_path(local):
        return local
    return root


def resolve_initial_scan_root(
    *,
    hint: str = "",
    remote_ip: str | None = None,
    default_ip: str = DEFAULT_REMOTE_IP,
) -> str:
    """Fast-open scan root: persisted session, EGM-local install, or remote UNC."""
    scan_root = (hint or "").strip()
    if not scan_root:
        from gui.sas_verify_dialog import load_persisted_scan_root
        from network.goldclub_paths import extract_ip_from_path as _extract_ip

        saved_root = load_persisted_scan_root()
        if saved_root:
            saved_host = (_extract_ip(saved_root) or "").strip()
            if not remote_ip or not saved_host or saved_host == remote_ip:
                scan_root = saved_root
    if not scan_root:
        from network.app_runtime import is_running_on_local_egm

        if is_running_on_local_egm():
            from network.goldclub_paths import (
                discover_local_game_image_scan_root,
                resolve_sas_verify_standalone_scan_root,
            )

            discovery = resolve_sas_verify_standalone_scan_root(remote_ip=remote_ip)
            scan_root = discovery.scan_root or ""
            if not scan_root and not discover_local_game_image_scan_root():
                scan_root = default_startup_scan_root(default_ip)
        else:
            from gui.sas_verify_dialog import load_persisted_cabinet_ip

            saved_ip = load_persisted_cabinet_ip()
            scan_root = default_startup_scan_root(saved_ip or default_ip)
    return prefer_local_cabinet_scan_root_on_egm(
        scan_root,
        remote_ip=remote_ip,
        user_hint=bool((hint or "").strip()),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "SAS accounting verification: SAS columns from live COM; "
            "Machine column from scan-root snapshot. On a workstation, "
            "auto scan-root prefers remote UNC even when a host COM is attached."
        ),
    )
    parser.add_argument(
        "--scan-root",
        default="",
        help=(
            "Log / state scan root for the Machine column (local or UNC). "
            "When omitted: opens with remote UNC for --ip / default cabinet IP."
        ),
    )
    parser.add_argument(
        "--ip",
        default="",
        help=f"Cabinet IP for remote UNC Machine path (default {DEFAULT_REMOTE_IP}).",
    )
    return parser.parse_args(argv)


def _show_opening_splash() -> QWidget:
    """Visible window before the meter dialog is constructed (no UNC/COM I/O)."""
    splash = QLabel("SAS Verify Meters\n\nOpening…")
    splash.setObjectName("sasVerifyOpeningSplash")
    splash.setAlignment(Qt.AlignmentFlag.AlignCenter)
    splash.setWindowTitle("SAS Verify Meters")
    splash.setWindowFlags(
        Qt.WindowType.Window
        | Qt.WindowType.CustomizeWindowHint
        | Qt.WindowType.WindowTitleHint
    )
    splash.setMinimumSize(420, 160)
    splash.resize(420, 180)
    splash.show()
    splash.repaint()
    return splash


def _prompt_local_scan_root(parent=None, *, reason: str = "") -> str:
    """Ask for a local D:\\ / USB folder after SAS/MUX and remote share failed."""
    detail = (reason or "").strip()
    body = (
        "Live SAS/MUX and the remote cabinet share are not available.\n\n"
        "Scan a local folder instead?\n"
        "Typical: run SasVerifyMeters.exe from USB on D:\\ and select "
        "Goldclub\\var (or a log_DD_MM_YYYY export folder)."
    )
    if detail:
        body = f"{detail}\n\n{body}"
    reply = QMessageBox.question(
        parent,
        "SAS Verify Meters",
        body,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return ""
    return QFileDialog.getExistingDirectory(
        parent,
        "Select local / USB scan root (usually on D:\\)",
        "D:\\",
    )


def run_sas_verify_app(argv: list[str] | None = None) -> int:
    """Launch only the SAS verify meters dialog (no log triage UI)."""
    args = _parse_args(argv)
    log_path = configure_app_logging(filename=SAS_VERIFY_LOG_NAME)
    log = get_logger(__name__)
    log.info("run_sas_verify_app starting log_file=%s", log_path)

    from gui.meter_timing_trace import enable_from_environment

    trace_file = enable_from_environment()
    if trace_file is not None:
        log.info("meter timing trace enabled path=%s", trace_file)

    app = QApplication.instance() or QApplication(sys.argv if argv is None else [])
    app.setApplicationName("Log Investigator")
    app.setApplicationDisplayName("SAS Verify Meters")
    app.setQuitOnLastWindowClosed(True)
    app.setStyle("Fusion")

    instance_lock = try_acquire_sas_verify_single_instance()
    if instance_lock is None:
        log.warning("second SasVerifyMeters instance refused — exiting")
        QMessageBox.warning(
            None,
            "Warning — only one instance",
            "Open only one SasVerifyMeters at a time. Two windows compete for "
            "COM and meter refreshing.\n\n"
            "Close the other SasVerifyMeters window, then try again.",
        )
        return 1
    app._sas_verify_instance_lock = instance_lock  # type: ignore[attr-defined]

    apply_app_icon(app)
    install_title_bar_theme_filter(app, SettingsManager.get_theme)
    apply_theme(app, SettingsManager.get_theme())

    hint = (args.scan_root or "").strip()
    remote_ip = (args.ip or "").strip() or extract_ip_from_path(hint) or None
    ip = remote_ip or DEFAULT_REMOTE_IP
    scan_root = resolve_initial_scan_root(
        hint=hint,
        remote_ip=remote_ip,
        default_ip=ip,
    )
    if scan_root != hint and not hint:
        from gui.sas_verify_dialog import load_persisted_scan_root

        saved = load_persisted_scan_root()
        if saved and scan_root != saved:
            log.info(
                "on EGM — prefer local install scan_root=%s (was %s)",
                scan_root,
                saved,
            )
        elif not saved:
            log.info("initial scan_root=%s remote_ip=%s", scan_root, ip)
    log.info(
        "fast open scan_root=%s remote_ip=%s log=%s",
        scan_root,
        ip,
        log_path,
    )

    splash = _show_opening_splash()
    try:
        log.info("constructing runtime")
        runtime = SasVerifyRuntime()
        from gui.sas_verify_dialog import SasVerifyDialog

        log.info("constructing dialog")
        dlg = SasVerifyDialog(
            runtime,
            runtime.thread_pool(),
            scan_root=scan_root,
            remote_ip=ip,
            parent=None,
        )
        log.info("dialog constructed")
    finally:
        splash.close()
        splash.deleteLater()

    dlg.setWindowModality(Qt.WindowModality.NonModal)
    dlg.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, True)
    dlg.setWindowTitle("SAS Verify Meters")
    dlg.show()
    # Paint chrome now. Do not processEvents() — that drains 0ms timers into
    # UNC/COM prefetch and leaves a white unpainted window.
    dlg.repaint()
    log.info("dialog shown")
    schedule_title_bar_theme(dlg, SettingsManager.get_theme())
    return app.exec()