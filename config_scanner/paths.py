"""Resolve config scanner directories (bundled assets + writable data)."""

from __future__ import annotations

import json
import os
import shutil
import string
import sys
from dataclasses import dataclass
from pathlib import Path


def _package_root() -> Path:
    return Path(__file__).resolve().parent


def bundled_assets_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "config_scanner" / "assets"  # type: ignore[attr-defined]
    return _package_root() / "assets"


def _migrate_legacy_nested_layout(exe_dir: Path) -> None:
    """Move data from exe_dir/config-scanner/ up to exe_dir/ (older portable builds)."""
    legacy = exe_dir / "config-scanner"
    if not legacy.is_dir():
        return
    for name in ("snapshots", "reports", "templates", "config.json", "baseline.json"):
        src = legacy / name
        if not src.exists():
            continue
        dest = exe_dir / name
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    try:
        if legacy.is_dir() and not any(legacy.iterdir()):
            legacy.rmdir()
    except OSError:
        pass


def _default_exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        from app_paths import app_install_dir

        return app_install_dir()
    return _package_root().parent / "config-scanner"


def _snapshot_entry_count(root: Path) -> int:
    snap_dir = root / "snapshots"
    if not snap_dir.is_dir():
        return 0
    try:
        return sum(1 for entry in snap_dir.iterdir() if not entry.name.startswith("."))
    except OSError:
        return 0


def _portable_data_candidates(exe_dir: Path) -> list[Path]:
    """Known QA USB / portable folders (lab default H:\\ConfigScanner first)."""
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(raw: str | Path | None) -> None:
        if not raw:
            return
        path = Path(raw).resolve()
        key = str(path).casefold()
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    add(os.environ.get("LOGINV_CONFIG_SCANNER_ROOT", "").strip())
    add("H:/ConfigScanner")
    for letter in string.ascii_uppercase:
        add(f"{letter}:/ConfigScanner")
    add(exe_dir)
    return candidates


def _resolve_tool_root() -> Path:
    """Writable data folder next to LogInvestigator / config-scanner (never another drive)."""
    exe_dir = _default_exe_dir()
    if getattr(sys, "frozen", False):
        _migrate_legacy_nested_layout(exe_dir)

    override = os.environ.get("LOGINV_CONFIG_SCANNER_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    return exe_dir


def tool_root() -> Path:
    """Writable folder for snapshots, reports, and seeded config (next to exe on USB)."""
    root = _resolve_tool_root()
    ensure_tool_data(root)
    return root


def ensure_tool_data(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    bundled = bundled_assets_root()
    seeds = [
        ("config.json", root / "config.json"),
        ("templates/report.html", root / "templates" / "report.html"),
    ]
    for rel, dest in seeds:
        src = bundled / rel
        if src.is_file() and not dest.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    for subdir in ("snapshots", "reports"):
        (root / subdir).mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class ToolConfig:
    game_drive: str | None
    build_version_relative_path: str
    scan_roots: list[str]
    include_patterns: list[str]
    parallel_workers: int
    snapshots_dir: str
    reports_dir: str


def load_tool_config(root: Path | None = None) -> ToolConfig:
    root = root or tool_root()
    config_path = root / "config.json"
    if not config_path.is_file():
        bundled = bundled_assets_root() / "config.json"
        if bundled.is_file():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled, config_path)
        else:
            raise FileNotFoundError(f"Config file not found: {config_path}")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return ToolConfig(
        game_drive=data.get("gameDrive"),
        build_version_relative_path=data.get(
            "buildVersionRelativePath", "ruleta\\BuildVersion.txt"
        ).replace("\\", "/"),
        scan_roots=list(data.get("scanRoots", ["config"])),
        include_patterns=list(
            data.get("includePatterns", ["*.xml", "*.ini", "*.conf", "*.json", "*.dat"])
        ),
        parallel_workers=int(data.get("parallelWorkers", 8)),
        snapshots_dir=data.get("snapshotsDir", "snapshots"),
        reports_dir=data.get("reportsDir", "reports"),
    )


def snapshots_path(root: Path | None = None) -> Path:
    cfg = load_tool_config(root)
    path = (root or tool_root()) / cfg.snapshots_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def reports_path(root: Path | None = None) -> Path:
    cfg = load_tool_config(root)
    path = (root or tool_root()) / cfg.reports_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def template_path(root: Path | None = None) -> Path:
    root = root or tool_root()
    local = root / "templates" / "report.html"
    if local.is_file():
        return local
    bundled = bundled_assets_root() / "templates" / "report.html"
    if bundled.is_file():
        return bundled
    raise FileNotFoundError(f"Report template not found under {root}")


def baseline_path(root: Path | None = None) -> Path:
    return (root or tool_root()) / "baseline.json"
