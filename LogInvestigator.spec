# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build recipe for the Log Investigator desktop UI.

Entry point: ``gui_app.py`` -> ``gui.main_window.run_app``.

Produces a single, windowed (no console) executable:

    dist/LogInvestigator.exe

Build profiles (choose with the LI_BUILD env var, default "lite"):

    lite  -> smallest binary. AI SDKs + OpenCV (QR scan) excluded; both
             features degrade gracefully at runtime.
    ai    -> lite + Google Gemini AI summary SDKs bundled.
    full  -> everything: AI SDKs + OpenCV/numpy (QR scanning) + pywin32,
             with google/grpc/protobuf collected via collect_all so the
             dynamic imports survive freezing. <-- "full stack features"

Examples (PowerShell):

    pyinstaller --noconfirm --clean LogInvestigator.spec                # lite
    $env:LI_BUILD = "full"; pyinstaller --noconfirm --clean LogInvestigator.spec

Or use the helper:  ./build_exe.ps1 -Full
(Back-compat: BUNDLE_AI=1 is treated as the "ai" profile.)
"""

import os

from PyInstaller.utils.hooks import collect_all, copy_metadata

# --- Resolve build profile -------------------------------------------------
_profile = (os.environ.get("LI_BUILD") or "").strip().lower()
if not _profile:
    _profile = "ai" if os.environ.get("BUNDLE_AI", "0") == "1" else "lite"

FULL = _profile == "full"
WITH_AI = _profile in ("ai", "full")
WITH_CV = FULL  # OpenCV/numpy power optional QR scanning

print(f"[LogInvestigator.spec] build profile = {_profile} "
      f"(AI={WITH_AI}, OpenCV={WITH_CV})")

# --- Runtime data files the app loads relative to its package root ----------
datas = [
    ("data/RouletteBonusMath.json", "data"),
    ("data/known_issues.json", "data"),
    ("issues/*.md", "issues"),
    ("issues/README.md", "issues"),
    ("cabinet_tools/InputAgent/InputAgent.cs", "cabinet_tools/InputAgent"),
]
binaries = []
hiddenimports = [
    "serial",
    "serial.tools",
    "serial.tools.list_ports",
]

# --- Base excludes: things never used by the app ---------------------------
excludes = [
    # Unused PySide6 / Qt modules (app uses only QtCore/QtGui/QtWidgets).
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
    # Other GUI toolkits that must never leak in.
    "tkinter", "PyQt5", "PyQt6", "PySide2",
    # Test framework.
    "pytest", "_pytest",
    # Heavy scientific libs not used anywhere.
    "matplotlib", "scipy", "pandas",
]

# --- Optional: OpenCV / numpy (QR scanning) --------------------------------
if WITH_CV:
    for _pkg in ("cv2", "numpy"):
        try:
            d, b, h = collect_all(_pkg)
            datas += d
            binaries += b
            hiddenimports += h
        except Exception as exc:  # noqa: BLE001 -- skip if not installed
            print(f"[LogInvestigator.spec] WARN: could not collect {_pkg}: {exc}")
else:
    excludes += ["cv2", "numpy", "PIL"]

# --- Optional: Google Gemini AI SDKs ---------------------------------------
if WITH_AI:
    _ai_pkgs = [
        "google.genai", "google.generativeai", "google.api_core",
        "google.protobuf", "grpc", "grpc_status", "proto", "googleapiclient",
    ]
    for _pkg in _ai_pkgs:
        try:
            d, b, h = collect_all(_pkg)
            datas += d
            binaries += b
            hiddenimports += h
        except Exception as exc:  # noqa: BLE001 -- skip if not installed
            print(f"[LogInvestigator.spec] WARN: could not collect {_pkg}: {exc}")
    # Some SDKs read their version via importlib.metadata at import time.
    for _dist in ("google-genai", "google-generativeai", "google-api-core",
                  "grpcio", "protobuf"):
        try:
            datas += copy_metadata(_dist)
        except Exception as exc:  # noqa: BLE001
            print(f"[LogInvestigator.spec] WARN: no metadata for {_dist}: {exc}")
else:
    excludes += [
        "google", "google.genai", "google.generativeai", "google.api_core",
        "google.protobuf", "grpc", "grpc_status", "proto", "googleapiclient",
    ]


a = Analysis(
    ["gui_app.py"],
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
    name="LogInvestigator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
