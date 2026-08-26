"""Game scan profiles (roulette USB, slot cabinet, etc.)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from config_scanner.paths import bundled_assets_root


@dataclass(frozen=True)
class ScanRootSpec:
    path: str
    recursive: bool = True


@dataclass(frozen=True)
class GameProfile:
    id: str
    label: str
    default_target: str
    build_version_relative_path: str | None
    build_fingerprint: dict[str, object] | None
    scan_roots: list[ScanRootSpec]
    include_patterns: list[str]
    discover_targets: list[str]
    parallel_workers: int = 8


def _parse_profile(data: dict[str, object]) -> GameProfile:
    roots: list[ScanRootSpec] = []
    for item in data.get("scanRoots") or []:
        if isinstance(item, str):
            roots.append(ScanRootSpec(path=item, recursive=True))
        elif isinstance(item, dict):
            roots.append(
                ScanRootSpec(
                    path=str(item.get("path", ".")),
                    recursive=bool(item.get("recursive", True)),
                )
            )
    fingerprint = data.get("buildFingerprint")
    fp = fingerprint if isinstance(fingerprint, dict) else None
    rel = data.get("buildVersionRelativePath")
    discover_raw = data.get("discoverTargets") or data.get("discover_targets") or []
    return GameProfile(
        id=str(data["id"]),
        label=str(data["label"]),
        default_target=str(data.get("defaultTarget") or ""),
        build_version_relative_path=str(rel) if rel else None,
        build_fingerprint=fp,
        scan_roots=roots or [ScanRootSpec(path="config", recursive=True)],
        include_patterns=list(
            data.get("includePatterns") or ["*.xml", "*.ini", "*.conf", "*.json", "*.dat"]
        ),
        discover_targets=[str(item) for item in discover_raw],
        parallel_workers=int(data.get("parallelWorkers") or 8),
    )


def load_profiles() -> list[GameProfile]:
    path = bundled_assets_root() / "profiles.json"
    if not path.is_file():
        return [
            GameProfile(
                id="roulette_usb",
                label="Ruleta Alegro Wing",
                default_target=r"\\10.0.0.90\c$\Goldclub",
                build_version_relative_path="ruleta/BuildVersion.txt",
                build_fingerprint=None,
                scan_roots=[
                    ScanRootSpec(path="config", recursive=True),
                    ScanRootSpec(path="bios/etc", recursive=True),
                    ScanRootSpec(path="data", recursive=True),
                ],
                include_patterns=["*.xml", "*.ini", "*.conf", "*.json", "*.dat"],
                discover_targets=[],
            )
        ]
    data = json.loads(path.read_text(encoding="utf-8"))
    return [_parse_profile(item) for item in data.get("profiles", [])]


def get_profile(profile_id: str) -> GameProfile:
    for profile in load_profiles():
        if profile.id == profile_id:
            return profile
    raise KeyError(f"Unknown config scanner profile: {profile_id}")


def default_profile_id() -> str:
    profiles = load_profiles()
    return profiles[0].id if profiles else "roulette_usb"


def is_unc_scan_target(target: str | None) -> bool:
    return bool(target) and str(target).lstrip().startswith("\\")


def display_profile_label(label: str | None, game_drive: str | None = None) -> str:
    """Human profile name without obsolete USB-D wording; annotate remote UNC."""
    raw = (label or "").strip() or "—"
    # Historical / misleading media-specific labels
    if raw in {
        "Roulette (USB D:)",
        "Roulette (USB)",
        "Roulette (USB D)",
        "Roulette",
    }:
        raw = "Ruleta Alegro Wing"
    if raw.startswith("Slot (lab"):
        raw = "Slot"
    gd = (game_drive or "").strip()
    if raw in {"—", "-"}:
        return raw
    if is_unc_scan_target(gd):
        # \\host\share\...
        parts = [p for p in gd.replace("/", "\\").split("\\") if p]
        host = parts[0] if parts else "remote"
        if not raw.endswith("(remote)") and host not in raw:
            return f"{raw} (remote {host})"
    return raw
