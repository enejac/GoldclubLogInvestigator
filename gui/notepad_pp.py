"""Open text files in portable or native Notepad++ (right-click and programmatic launch)."""

from __future__ import annotations


import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QPlainTextEdit, QTextEdit, QWidget

NPP_PORTABLE_ROOT = Path(r"H:\npp.8.7.7.portable.x64")
NPP_PORTABLE_ROOT_ENV = "LOGINV_NPP_ROOT"
NPP_PORTABLE_FOLDER_NAMES = (
    "npp.8.7.7.portable.x64",
    "npp.portable.x64",
)

_TEXT_SUFFIXES = frozenset(
    {
        ".log",
        ".txt",
        ".dat",
        ".md",
        ".xml",
        ".json",
        ".jsonl",
        ".conf",
        ".ini",
        ".ps1",
        ".bat",
        ".cmd",
        ".py",
        ".csv",
        ".html",
        ".htm",
        ".yaml",
        ".yml",
        ".properties",
        ".cfg",
        ".tsv",
        ".reg",
        ".sql",
    }
)

_NPP_EXE_NAMES = ("notepad++.exe", "Notepad++.exe")


def _portable_root() -> Path:
    """Primary portable Notepad++ folder; override with LOGINV_NPP_ROOT."""
    return _portable_search_roots()[0]

def _portable_search_roots() -> list[Path]:
    """Candidate portable Notepad++ install folders (USB, EGM D:, exe drive)."""
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = os.path.normcase(str(path))
        if key in seen:
            return
        seen.add(key)
        roots.append(path)

    override = (os.environ.get(NPP_PORTABLE_ROOT_ENV) or "").strip()
    if override:
        _add(Path(override))

    drives: list[str] = []
    for drive in (r"H:", r"D:", _executable_drive()):
        if drive and drive not in drives:
            drives.append(drive)

    for drive in drives:
        for name in NPP_PORTABLE_FOLDER_NAMES:
            _add(Path(drive) / name)

    try:
        exe_drive = Path(sys.executable).resolve().anchor
        if exe_drive:
            for name in NPP_PORTABLE_FOLDER_NAMES:
                _add(Path(exe_drive) / name)
    except OSError:
        pass

    if not roots:
        _add(NPP_PORTABLE_ROOT)
    return roots


def _is_windows() -> bool:
    return sys.platform == "win32" or os.name == "nt"


def _executable_drive() -> str:
    try:
        return Path(sys.executable).drive
    except (OSError, TypeError, ValueError):
        return ""


def _is_removable_drive(drive: str) -> bool:
    if not _is_windows() or not drive:
        return False
    try:
        import ctypes

        drive_root = drive if drive.endswith("\\") else f"{drive}\\"
        return ctypes.windll.kernel32.GetDriveTypeW(drive_root) == 2  # DRIVE_REMOVABLE
    except (AttributeError, OSError, ValueError):
        return False


def _running_from_removable_drive() -> bool:
    return _is_removable_drive(_executable_drive())


def _first_existing_exe(paths: Iterable[Path]) -> Path | None:
    seen: set[str] = set()
    for path in paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _exe_candidates_in_dir(directory: Path) -> tuple[Path, ...]:
    return tuple(directory / name for name in _NPP_EXE_NAMES)


def _registry_npp_install_dirs() -> list[Path]:
    if not _is_windows():
        return []
    try:
        import winreg
    except ImportError:
        return []

    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    dirs: list[Path] = []
    seen: set[str] = set()
    for hive, subkey in roots:
        try:
            with winreg.OpenKey(hive, subkey) as root:
                subkey_count = winreg.QueryInfoKey(root)[0]
                for index in range(subkey_count):
                    try:
                        entry_name = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, entry_name) as entry:
                            try:
                                display_name = str(winreg.QueryValueEx(entry, "DisplayName")[0])
                            except OSError:
                                continue
                            if "notepad++" not in display_name.casefold():
                                continue
                            try:
                                install_location = str(
                                    winreg.QueryValueEx(entry, "InstallLocation")[0]
                                ).strip()
                            except OSError:
                                continue
                            if not install_location:
                                continue
                            normalized = os.path.normcase(os.path.normpath(install_location))
                            if normalized in seen:
                                continue
                            seen.add(normalized)
                            dirs.append(Path(install_location))
                    except OSError:
                        continue
        except OSError:
            continue
    return dirs


def _native_install_dirs() -> list[Path]:
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = os.path.normcase(str(path))
        if key in seen:
            return
        seen.add(key)
        dirs.append(path)

    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            _add(Path(base) / "Notepad++")

    _add(Path(r"C:\Program Files\Notepad++"))
    _add(Path(r"C:\Program Files (x86)\Notepad++"))

    for path in _registry_npp_install_dirs():
        _add(path)
    return dirs


def _find_portable_npp_exe_in_root(root: Path) -> Path | None:
    direct = _first_existing_exe(_exe_candidates_in_dir(root))
    if direct is not None:
        return direct
    try:
        if not root.is_dir():
            return None
        for pattern in _NPP_EXE_NAMES:
            for path in root.rglob(pattern):
                if path.is_file():
                    return path
    except OSError:
        return None
    return None


def _find_portable_npp_exe() -> Path | None:
    for root in _portable_search_roots():
        found = _find_portable_npp_exe_in_root(root)
        if found is not None:
            return found
    return None


def _native_npp_exe_candidates() -> list[Path]:
    candidates: list[Path] = []
    for directory in _native_install_dirs():
        candidates.extend(_exe_candidates_in_dir(directory))
    return candidates


def resolve_notepad_pp_exe() -> Path | None:
    """Return Notepad++ executable from portable or native install, if present."""
    portable = _find_portable_npp_exe()
    native = _first_existing_exe(_native_npp_exe_candidates())

    if _running_from_removable_drive():
        return portable or native
    return native or portable


def is_text_file_path(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        suffix = Path(path).suffix.lower()
    except (TypeError, ValueError):
        return False
    return suffix in _TEXT_SUFFIXES or suffix == ""


def open_with_notepad_pp(path: str | Path) -> tuple[bool, str]:
    """Launch Notepad++ for an existing text file. Returns (ok, message)."""
    if not _is_windows():
        return False, "Notepad++ launch is only supported on Windows."

    raw = str(path or "").strip()
    if not raw:
        return False, "No file path specified."

    normalized = os.path.normpath(raw)
    file_path = Path(normalized)
    try:
        if not file_path.is_file():
            return False, f"File not found or not reachable:\n{normalized}"
    except OSError as e:
        return False, f"Could not access path:\n{normalized}\n\n{e}"

    npp = resolve_notepad_pp_exe()
    if npp is None:
        checked = ", ".join(str(p) for p in _portable_search_roots())
        return (
            False,
            "Notepad++ not found. Checked portable installs "
            f"({checked}) and common native install locations.",
        )

    try:
        subprocess.Popen([str(npp), normalized], shell=False, close_fds=True)
    except OSError as e:
        return False, f"Could not start Notepad++:\n{e}"
    return True, f"Opened in Notepad++:\n{normalized}"


def open_with_notepad_or_npp(path: str | Path) -> tuple[bool, str]:
    """Open a text file in Notepad++ when installed, else fall back to plain Notepad."""
    if not _is_windows():
        return False, "Opening files is only supported on Windows."

    raw = str(path or "").strip()
    if not raw:
        return False, "No file path specified."

    normalized = os.path.normpath(raw)
    file_path = Path(normalized)
    try:
        if not file_path.is_file():
            return False, f"File not found or not reachable:\n{normalized}"
    except OSError as e:
        return False, f"Could not access path:\n{normalized}\n\n{e}"

    npp = resolve_notepad_pp_exe()
    if npp is not None:
        try:
            subprocess.Popen([str(npp), normalized], shell=False, close_fds=True)
            return True, f"Opened in Notepad++:\n{normalized}"
        except OSError as e:
            return False, f"Could not start Notepad++:\n{e}"

    try:
        subprocess.Popen(["notepad.exe", normalized], shell=False, close_fds=True)
    except OSError as e:
        return False, f"Could not start Notepad:\n{e}"
    return True, f"Opened in Notepad:\n{normalized}"


def create_open_with_npp_action(
    parent: QWidget,
    path_provider: Callable[[], str | Path | None],
    *,
    label: str = "Open with Notepad++",
) -> QAction:
    action = QAction(label, parent)

    def _open() -> None:
        path = path_provider()
        if path is None:
            return
        ok, msg = open_with_notepad_pp(path)
        if not ok:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(parent, "Notepad++", msg)

    action.triggered.connect(_open)
    return action


def attach_open_with_npp_menu(
    widget: QPlainTextEdit | QTextEdit,
    *,
    path_provider: Callable[[], str | Path | None],
    parent: QWidget,
    label: str = "Open with Notepad++",
) -> None:
    """Add a right-click item to open the associated text file in Notepad++."""

    def _on_context_menu(pos) -> None:  # noqa: ANN001
        menu = widget.createStandardContextMenu()
        path = path_provider()
        if path is not None and is_text_file_path(path):
            try:
                if Path(path).is_file():
                    act = create_open_with_npp_action(parent, lambda: path, label=label)
                    menu.addSeparator()
                    menu.addAction(act)
            except OSError:
                pass
        menu.exec(widget.mapToGlobal(pos))

    from PySide6.QtCore import Qt

    widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    widget.customContextMenuRequested.connect(_on_context_menu)


def extend_menu_with_npp_action(
    menu: QMenu,
    parent: QWidget,
    path_provider: Callable[[], str | Path | None],
    *,
    label: str = "Open with Notepad++",
) -> None:
    """Append Notepad++ action to an existing QMenu when the path is a text file."""
    path = path_provider()
    if path is None or not is_text_file_path(path):
        return
    try:
        if not Path(path).is_file():
            return
    except OSError:
        return
    menu.addSeparator()
    menu.addAction(create_open_with_npp_action(parent, path_provider, label=label))
