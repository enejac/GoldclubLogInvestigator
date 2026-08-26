"""Open Config Scanner HTML reports in a real browser (portable Chromium first)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from config_scanner.paths import _default_exe_dir, _portable_data_candidates, _resolve_tool_root

BROWSER_ENV = "LOGINV_HTML_BROWSER"
_CHROME_EXE_NAMES = ("chrome.exe", "chromium.exe", "msedge.exe", "brave.exe", "thorium.exe")


def _is_windows() -> bool:
    return os.name == "nt"


def _add_unique(paths: list[Path], seen: set[str], path: Path | None) -> None:
    if path is None:
        return
    key = os.path.normcase(str(path))
    if key in seen:
        return
    seen.add(key)
    paths.append(path)


def portable_browser_roots() -> list[Path]:
    """Folders that may contain chrome-win64\\chrome.exe (USB, D:, next to the exe)."""
    roots: list[Path] = []
    seen: set[str] = set()

    override = (os.environ.get("LOGINV_CONFIG_SCANNER_ROOT") or "").strip()
    if override:
        _add_unique(roots, seen, Path(override) / "browser")

    _add_unique(roots, seen, _resolve_tool_root() / "browser")
    for candidate in _portable_data_candidates(_default_exe_dir()):
        _add_unique(roots, seen, candidate / "browser")
    return roots


def _find_chrome_exe_in_root(root: Path) -> Path | None:
    preferred = root / "chrome-win64" / "chrome.exe"
    try:
        if preferred.is_file():
            return preferred
    except OSError:
        return None
    for name in _CHROME_EXE_NAMES:
        direct = root / name
        try:
            if direct.is_file():
                return direct
        except OSError:
            continue
    try:
        if not root.is_dir():
            return None
        for child in root.iterdir():
            if not child.is_dir():
                continue
            for name in _CHROME_EXE_NAMES:
                nested = child / name
                if nested.is_file():
                    return nested
    except OSError:
        return None
    return None


def _env_browser_exe() -> Path | None:
    raw = (os.environ.get(BROWSER_ENV) or "").strip()
    if not raw:
        return None
    path = Path(raw)
    try:
        if path.is_file():
            return path
        if path.is_dir():
            return _find_chrome_exe_in_root(path)
    except OSError:
        return None
    return None


def installed_browser_candidates() -> list[Path]:
    """System Chrome / Edge install locations (Windows)."""
    candidates: list[Path] = []
    seen: set[str] = set()
    program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
    program_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    local_app = os.environ.get("LOCALAPPDATA") or ""
    for raw in (
        Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(local_app) / "Google" / "Chrome" / "Application" / "chrome.exe" if local_app else None,
        Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(local_app) / "Microsoft" / "Edge" / "Application" / "msedge.exe" if local_app else None,
    ):
        _add_unique(candidates, seen, raw)
    return candidates


def _existing_files(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            if path.is_file():
                _add_unique(found, seen, path)
        except OSError:
            continue
    return found


def iter_html_browsers() -> list[Path]:
    """Portable Chromium first, then installed Chrome/Edge."""
    ordered: list[Path] = []
    seen: set[str] = set()
    env_exe = _env_browser_exe()
    if env_exe is not None:
        _add_unique(ordered, seen, env_exe)
    for root in portable_browser_roots():
        found = _find_chrome_exe_in_root(root)
        if found is not None:
            _add_unique(ordered, seen, found)
    for exe in _existing_files(installed_browser_candidates()):
        _add_unique(ordered, seen, exe)
    return ordered


def resolve_html_browser() -> Path | None:
    browsers = iter_html_browsers()
    return browsers[0] if browsers else None


def is_portable_browser(exe: Path) -> bool:
    """True when the exe lives under a Config Scanner browser\\ folder."""
    parts = {part.casefold() for part in exe.parts}
    return "browser" in parts


def browser_launch_args(exe: Path, html_path: Path) -> list[str]:
    args = [str(exe)]
    if is_portable_browser(exe):
        user_data = exe.parent.parent / "user-data"
        if exe.parent.name.casefold() != "chrome-win64":
            user_data = exe.parent / "user-data"
        args.extend(
            [
                f"--user-data-dir={user_data}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-default-apps",
            ]
        )
    args.append(str(html_path))
    return args


def _start_file(path: Path) -> None:
    os.startfile(str(path))  # type: ignore[attr-defined]


def open_html_file(path: str | Path) -> tuple[bool, str]:
    """Launch an HTML file in Chromium/Chrome/Edge, else the Windows file association."""
    raw = str(path or "").strip()
    if not raw:
        return False, "No report path specified."
    html_path = Path(os.path.normpath(raw))
    try:
        if not html_path.is_file():
            return False, f"Report file not found:\n{html_path}"
    except OSError as exc:
        return False, f"Could not access report:\n{html_path}\n\n{exc}"

    html_path = html_path.resolve()
    errors: list[str] = []
    for exe in iter_html_browsers():
        cmd = browser_launch_args(exe, html_path)
        try:
            subprocess.Popen(cmd, shell=False, close_fds=True)
            return True, f"Opened in {exe.name}:\n{html_path}"
        except OSError as exc:
            errors.append(f"{exe}: {exc}")

    if _is_windows():
        try:
            _start_file(html_path)
            return True, f"Opened with the default app:\n{html_path}"
        except OSError as exc:
            errors.append(f"startfile: {exc}")
    else:
        try:
            import webbrowser

            if webbrowser.open(html_path.as_uri()):
                return True, f"Opened in the default browser:\n{html_path}"
        except OSError as exc:
            errors.append(str(exc))

    detail = "\n".join(errors) if errors else "No Chromium/Chrome/Edge install was found."
    searched = ", ".join(str(root) for root in portable_browser_roots()[:4])
    return (
        False,
        "Could not open the report in a browser.\n\n"
        f"{html_path}\n\n"
        f"{detail}\n\n"
        "Put Chrome for Testing in config-scanner\\browser\\chrome-win64\\chrome.exe "
        f"(searched: {searched}) or install Chrome/Edge. "
        "Fetch with: python scripts/fetch_config_scanner_browser.py",
    )
