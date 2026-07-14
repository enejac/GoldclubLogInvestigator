"""Resolve config scanner directories (bundled assets + writable data)."""

from __future__ import annotations

import json
import shutil
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


def tool_root() -> Path:
    """Writable folder for snapshots, reports, and seeded config (next to exe on USB)."""
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        _migrate_legacy_nested_layout(root)
    else:
        root = _package_root().parent / "config-scanner"
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
