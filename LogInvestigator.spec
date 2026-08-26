# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build recipe for the Log Investigator desktop UI.

Entry point: ``gui_app.py`` -> ``gui.main_window.run_app``.

Produces a single, windowed (no console) executable:

    dist/LogInvestigator.exe

Build profiles (choose with the LI_BUILD env var, default "lite"):

    lite      -> smallest binary. AI SDKs + OpenCV (QR scan) excluded; pywin32
                 included for exe ProductVersion reads in Config Scanner.
    ai        -> lite + Google Gemini AI summary SDKs bundled.
    local_ai  -> lite + llama-cpp-python for offline AI Helper (GGUF external).
    full      -> everything: AI SDKs + OpenCV/numpy (QR scanning) + pywin32,
                 with google/grpc/protobuf collected via collect_all so the
                 dynamic imports survive freezing. Local AI collected if installed.

Examples (PowerShell):

    pyinstaller --noconfirm --clean LogInvestigator.spec                # lite
    $env:LI_BUILD = "full"; pyinstaller --noconfirm --clean LogInvestigator.spec

Or use the helper:  ./build_exe.ps1 -Full
(Back-compat: BUNDLE_AI=1 is treated as the "ai" profile.)
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all, copy_metadata

# --- Resolve build profile -------------------------------------------------
_profile = (os.environ.get("LI_BUILD") or "").strip().lower()
if not _profile:
    _profile = "ai" if os.environ.get("BUNDLE_AI", "0") == "1" else "lite"

FULL = _profile == "full"
WITH_AI = _profile in ("ai", "full")
WITH_LOCAL_AI = _profile in ("local_ai", "full")
WITH_CV = FULL  # OpenCV powers optional QR scanning

print(f"[LogInvestigator.spec] build profile = {_profile} "
      f"(AI={WITH_AI}, LocalAI={WITH_LOCAL_AI}, OpenCV={WITH_CV})")

# --- Runtime data files the app loads relative to its package root ----------
datas = [
("data/RouletteBonusMath.json", "data"),
("data/known_issues.json", "data"),
("data/roulette_error_catalog.json", "data"),
("data/ai_helper_config_map.json", "data"),
    ("issues/*.md", "issues"),
    ("issues/README.md", "issues"),
    ("cabinet_tools/InputAgent/InputAgent.cs", "cabinet_tools/InputAgent"),
    # Software Version swap (non-injection roulette helpers only).
    ("cabinet_tools/roulette/Kill-All.ps1", "cabinet_tools/roulette"),
    ("cabinet_tools/roulette/Run-FullStack.ps1", "cabinet_tools/roulette"),
    ("cabinet_tools/roulette/Clear-Error30.ps1", "cabinet_tools/roulette"),
    ("cabinet_tools/roulette/Clear-TrialPersistent.ps1", "cabinet_tools/roulette"),
    ("cabinet_tools/roulette/Fix-Error30Clock.ps1", "cabinet_tools/roulette"),
    ("cabinet_tools/roulette/Invoke-SoftwareVersionSwap.ps1", "cabinet_tools/roulette"),
    ("cabinet_tools/roulette/GoldClubServices.ps1", "cabinet_tools/roulette"),
    ("cabinet_tools/roulette/Convert-GcxmlSetup.ps1", "cabinet_tools/roulette"),
    ("assets/log_investigator_icon.png", "assets"),
    ("config_scanner/assets/config.json", "config_scanner/assets"),
    ("config_scanner/assets/profiles.json", "config_scanner/assets"),
    ("config_scanner/assets/templates/report.html", "config_scanner/assets/templates"),
    # Roulette automation hitboxes / calibration (loaded relative to automation package).
    ("automation/layouts/*.json", "automation/layouts"),
    ("automation/bot_config.json", "automation"),
    ("automation/xray_recipes/*", "automation/xray_recipes"),
]
# Never bundle lab inject tooling (Dallas splice / WinDivert AFT / tactic-c probes).
# Those stay as repo-side scripts only — not part of LogInvestigator.exe.
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
            f"[LogInvestigator.spec] REFUSING to bundle forbidden inject path: {_src}"
        )
binaries = []
hiddenimports = [
    "gui.app_logging",
    "gui.automation_tab",
    "gui.automation_worker",
    "gui.lazy_tab_placeholder",
    "gui.thin_progress",
    "automation",
    "automation.tutankhamen_symbols",
    "automation.roulette_strategies",
    "automation.roulette_layout",
    "automation.roulette_surface",
    "automation.roulette_layout_store",
    "automation.roulette_ui_areas",
    "automation.roulette_layout2_areas",
    "automation.runner",
    "automation.roulette_runner",
    "automation.roulette_board_mapper",
    "automation.roulette_random_bot",
    "automation.bot_config",
    "automation.click_logger",
    "automation.xray_recipe",
    "automation.run_jira_repro",
    "network.jira_client",
    "network.xray_client",
    "gui.bot_config_dialog",
    "automation.roulette_middleware",
    "automation.remote_input_agent",
    # Map current screen: identify, relocate, render the review overlay, prove.
    "automation.roulette_screen_map",
    "automation.roulette_map_overlay",
    "automation.roulette_verify_ui",
    "automation.roulette_verify_bets",
    "automation.roulette_edge_probe",
    "automation.roulette_target",
    "automation.local_input_agent",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "serial",
    "serial.tools",
    "serial.tools.list_ports",
    # Config scanner + main app read ProductVersion from game exes on Windows.
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
            print(f"[LogInvestigator.spec] WARN: could not collect {_win_mod}: {exc}")

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

# --- numpy + Pillow: required, not optional ---------------------------------
# The roulette screen mapper compares screenshots pixel by pixel and draws the
# review overlay, so numpy and Pillow ship in every profile. Excluding them (as
# the lite profile once did) leaves "Map current screen" raising ImportError the
# moment an operator clicks it.
for _pkg in ("numpy", "PIL"):
    try:
        d, b, h = collect_all(_pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # noqa: BLE001 -- skip if not installed
        print(f"[LogInvestigator.spec] WARN: could not collect {_pkg}: {exc}")

# --- Optional: OpenCV (QR scanning) ----------------------------------------
if WITH_CV:
    try:
        d, b, h = collect_all("cv2")
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # noqa: BLE001 -- skip if not installed
        print(f"[LogInvestigator.spec] WARN: could not collect cv2: {exc}")
else:
    excludes += ["cv2"]

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

# --- Optional: llama-cpp-python (offline AI Helper) -------------------------
if WITH_LOCAL_AI:
    for _pkg in ("llama_cpp",):
        try:
            d, b, h = collect_all(_pkg)
            datas += d
            binaries += b
            hiddenimports += h
        except Exception as exc:  # noqa: BLE001
            print(f"[LogInvestigator.spec] WARN: could not collect {_pkg}: {exc}")
    try:
        datas += copy_metadata("llama-cpp-python")
    except Exception as exc:  # noqa: BLE001
        print(f"[LogInvestigator.spec] WARN: no metadata for llama-cpp-python: {exc}")
    # Prefer vendor Release CRT DLLs (PyPI win wheels may ship Debug CRT / *D.dll).
    _vendor_lib = os.path.join(SPECPATH, "vendor", "llama_cpp_lib")
    _lib_dirs = []
    if os.path.isdir(_vendor_lib) and os.path.isfile(os.path.join(_vendor_lib, "llama.dll")):
        _lib_dirs.append(_vendor_lib)
        print(f"[LogInvestigator.spec] using vendor Release DLLs: {_vendor_lib}")
    try:
        import llama_cpp as _llama_cpp_pkg

        _pkg_lib = os.path.join(os.path.dirname(_llama_cpp_pkg.__file__), "lib")
        if os.path.isdir(_pkg_lib):
            _lib_dirs.append(_pkg_lib)
    except Exception as exc:  # noqa: BLE001
        print(f"[LogInvestigator.spec] WARN: llama_cpp package lib: {exc}")
    _seen_dll = set()
    for _lib_dir in _lib_dirs:
        for _name in sorted(os.listdir(_lib_dir)):
            if not _name.lower().endswith(".dll"):
                continue
            _key = _name.lower()
            if _key in _seen_dll:
                continue
            _seen_dll.add(_key)
            _src = os.path.join(_lib_dir, _name)
            binaries.append((_src, "llama_cpp/lib"))
            print(f"[LogInvestigator.spec] llama DLL -> llama_cpp/lib/{_name}")
    hiddenimports += [
        "ai_helper",
        "ai_helper.agent",
        "ai_helper.engine",
        "ai_helper.retrieve",
        "ai_helper.config_map",
        "ai_helper.prompts",
        "llama_cpp",
        "llama_cpp.llama",
        "llama_cpp.llama_cpp",
    ]
else:
    # Keep ai_helper (search-only) in the lite build; exclude native llama only.
    hiddenimports += [
        "ai_helper",
        "ai_helper.agent",
        "ai_helper.engine",
        "ai_helper.retrieve",
        "ai_helper.config_map",
        "ai_helper.prompts",
        "ai_helper.gcxml_decrypt",
    ]

_runtime_hooks = []
if WITH_LOCAL_AI:
    _rth = os.path.join(SPECPATH, "hooks", "pyi_rth_llama_cpp.py")
    if os.path.isfile(_rth):
        _runtime_hooks.append(_rth)
        print(f"[LogInvestigator.spec] runtime hook {_rth}")

a = Analysis(
    ["gui_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=_runtime_hooks,
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
