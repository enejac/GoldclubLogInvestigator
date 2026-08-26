"""Track the last bulk-restore rollback point for one-click revert."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from config_scanner.paths import snapshots_path, tool_root
from config_scanner.scanner import save_json, snapshot_content_root


def rollback_path(root: Path | None = None) -> Path:
    return (root or tool_root()) / "rollback.json"


@dataclass(frozen=True)
class RollbackInfo:
    """Live config captured immediately before a bulk restore."""

    snapshot_name: str
    restored_from: str
    write_scope: str
    scan_target: str
    recorded_at: str

    @classmethod
    def from_dict(cls, data: dict) -> RollbackInfo | None:
        name = (data.get("snapshotName") or data.get("snapshot_name") or "").strip()
        if not name:
            return None
        return cls(
            snapshot_name=name,
            restored_from=(data.get("restoredFrom") or data.get("restored_from") or "").strip(),
            write_scope=(data.get("writeScope") or data.get("write_scope") or "full").strip(),
            scan_target=(data.get("scanTarget") or data.get("scan_target") or "").strip(),
            recorded_at=(data.get("recordedAt") or data.get("recorded_at") or "").strip(),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "snapshotName": self.snapshot_name,
            "restoredFrom": self.restored_from,
            "writeScope": self.write_scope,
            "scanTarget": self.scan_target,
            "recordedAt": self.recorded_at,
        }


def load_rollback(root: Path | None = None) -> RollbackInfo | None:
    path = rollback_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return RollbackInfo.from_dict(data)


def save_rollback(info: RollbackInfo, root: Path | None = None) -> None:
    save_json(rollback_path(root), info.to_dict())


def clear_rollback(root: Path | None = None) -> None:
    path = rollback_path(root)
    if path.is_file():
        path.unlink(missing_ok=True)


def rollback_snapshot_dir(info: RollbackInfo, root: Path | None = None) -> Path:
    return snapshots_path(root) / info.snapshot_name


def rollback_is_available(root: Path | None = None, info: RollbackInfo | None = None) -> bool:
    """True when rollback metadata points at an archived snapshot folder."""
    entry = info or load_rollback(root)
    if entry is None:
        return False
    snap_dir = rollback_snapshot_dir(entry, root)
    if not snap_dir.is_dir():
        return False
    return snapshot_content_root(snap_dir) is not None


@dataclass(frozen=True)
class RollbackDetails:
    """What the undo point actually puts back."""

    version: str
    has_software: bool

    @property
    def is_self_contained(self) -> bool:
        """True when reverting does not depend on a software_versions pack."""
        return self.has_software

    def summary(self) -> str:
        software = (
            "config + Ruleta binaries" if self.has_software else "config only"
        )
        return f"Ruleta {self.version or '?'}, {software}"


def describe_rollback(
    info: RollbackInfo | None,
    root: Path | None = None,
) -> RollbackDetails | None:
    """Version and completeness of the archived rollback snapshot."""
    if info is None:
        return None
    from config_scanner.scanner import load_build_info
    from config_scanner.software_compat import snapshot_has_embedded_software

    snap_dir = rollback_snapshot_dir(info, root)
    if not snap_dir.is_dir():
        return None
    try:
        build_info = load_build_info(snap_dir)
    except (OSError, ValueError):
        return None
    version = (
        build_info.exe_product_version or build_info.product_version or ""
    ).strip()
    return RollbackDetails(
        version=version,
        has_software=snapshot_has_embedded_software(snap_dir),
    )


def make_rollback_info(
    *,
    snapshot_name: str,
    restored_from: str,
    write_scope: str,
    scan_target: str,
) -> RollbackInfo:
    return RollbackInfo(
        snapshot_name=snapshot_name.strip(),
        restored_from=restored_from.strip(),
        write_scope=(write_scope or "full").strip(),
        scan_target=(scan_target or "").strip(),
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )