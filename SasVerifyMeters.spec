# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller recipe for the SAS Verify Meters standalone tool.

Entry: ``sas_verify_app.py`` -> ``gui.sas_verify_app.run_sas_verify_app``.

Output:

    SasVerifyMeters.exe   (repo root — not dist/)

Leaner than LogInvestigator: no Automation tab UI, Config Scanner assets, AI SDKs,
or OpenCV. Still includes pyserial + pywin32 for COM meter capture, plus the thin
``automation.remote_exec`` slice needed by RAM Clear (WinRM/PsExec).
"""

import sys

from PyInstaller.utils.hooks import collect_all

print("[SasVerifyMeters.spec] building SAS verify meters standalone")

datas = [
    ("assets/log_investigator_icon.png", "assets"),
]
binaries = []
hiddenimports = [
    "gui.sas_verify_app",
    "gui.sas_verify_dialog",
    "gui.thin_progress",
    "gui.sas_money_format",
    "gui.machine_yield_chart",
    "gui.view_model",
    "gui.app_branding",
    "gui.app_logging",
    "gui.theme_utils",
    "gui.win_title_bar",
    "gui.palette_adapt",
    "gui.ram_clear_worker",
    "gui.ram_clear_ui",
    "network.goldclub_paths",
    "network.accounting_state_loader",
    "network.accounting_scanner",
    "network.sas_serial_meters",
    "network.sas_parser",
    "network.meter_comparator",
    "network.health_monitor",
    "network.scanner_utils",
    "network.lab_access",
    "network.ram_clear",
    # RAM Clear remote path (dialog imports this at startup via ram_clear_worker).
    "automation",
    "automation.remote_exec",
    "serial",
    "serial.tools",
    "serial.tools.list_ports",
    "win32api",
    "pywintypes",
]

if sys.platform == "win32":
    for _win_mod in ("win32api", "pywintypes", "win32ctypes"):
        try:
            _win_d, _win_b, _win_h = collect_all(_win_mod)
            datas += _win_d
            binaries += _win_b
            hiddenimports += _win_h
        except Exception as exc:  # noqa: BLE001
            print(f"[SasVerifyMeters.spec] WARN: could not collect {_win_mod}: {exc}")

excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2", "PySide6.QtQuickTest",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras", "PySide6.Qt3DLogic",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtGraphsWidgets",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtUiTools",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSerialPort", "PySide6.QtSensors",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine",
    "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtHelp",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtNetworkAuth", "PySide6.QtTextToSpeech", "PySide6.QtDBus",
    "tkinter", "PyQt5", "PyQt6", "PySide2",
    "pytest", "_pytest",
    "matplotlib", "scipy", "pandas",
    "cv2", "numpy", "PIL",
    "google", "google.genai", "google.generativeai", "google.api_core",
    "google.protobuf", "grpc", "grpc_status", "proto", "googleapiclient",
    "llama_cpp",
    # Keep the Automation *tab* / roulette bot stack out; do NOT exclude the
    # ``automation`` package itself — RAM Clear imports automation.remote_exec.
    "gui.automation_tab",
    "gui.automation_worker",
    "gui.main_window",
    "gui.config_scanner_window",
    "gui.config_scanner_tab",
    "gui.ai_helper_window",
    "ai_helper",
    "automation.roulette_runner",
    "automation.roulette_board_mapper",
    "automation.roulette_random_bot",
    "automation.runner",
    "automation.run_jira_repro",
]

_FORBIDDEN_DATA_SUBSTR = (
    "dallas",
    "windivert",
    "wdinject",
    "wdpollinject",
    "invoke-windivertaft",
    "send-testaft",
    "tactic-c-credit",
    "dallassplice",
)
for _src, _dest in datas:
    _low = str(_src).replace("\\", "/").lower()
    if any(x in _low for x in _FORBIDDEN_DATA_SUBSTR):
        raise SystemExit(
            f"[SasVerifyMeters.spec] REFUSING to bundle forbidden inject path: {_src}"
        )

a = Analysis(
    ["sas_verify_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SasVerifyMeters",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/log_investigator.ico",
)
