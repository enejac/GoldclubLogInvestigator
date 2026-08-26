"""Compare snapshot Ruleta version with the live Ruleta.exe PE version.

BuildVersion.txt can lag a surgical binary swap (GRT330106 still said 40114
after Ruleta 10.1 was copied in). Prefer the exe ProductVersion.

Full snapshots also copy the surgical Ruleta binaries into ``software/``
so restore does not depend on a matching folder under software_versions.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from config_scanner.build_version import (
    BuildInfo,
    detect_roulette_exe_version,
    scan_target_path,
    short_product_version,
)


class SoftwarePushError(OSError):
    """Config + Ruleta software cannot continue without a successful pack copy."""


SNAPSHOT_SOFTWARE_SUBDIR = "software"


@dataclass(frozen=True)
class SoftwareCaptureResult:
    """Outcome of copying live Ruleta binaries into a snapshot."""

    captured: bool
    file_count: int
    missing: tuple[str, ...]
    dest: Path | None
    note: str = ""


def is_software_capture_info_note(note: str) -> bool:
    """True for a successful capture line — not an operator warning."""
    text = (note or "").strip()
    if not (
        text.startswith("Captured ")
        and "Ruleta software files into the snapshot" in text
    ):
        return False
    return "needs 64-bit" not in text


def scan_user_warnings(warnings: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Warnings that should surface in the UI (drop success chatter)."""
    return tuple(
        text
        for text in ((item or "").strip() for item in warnings)
        if text and not is_software_capture_info_note(text)
    )


def pack_refuse_reason(pack: Path | None) -> str | None:
    """Why ``binaries_only`` must not push this pack, or None if allowed."""
    if pack is None:
        return None
    name = pack.name
    if "10.2.0.684" in name:
        return f"Refusing {name}: ERROR 30 Development trial / LLAVE."
    if "10.2.0.0" in name:
        return f"Refusing {name}: silent ProcessExit (Downloads 10.2.0.0)."
    return None


def snapshot_ruleta_major_minor(info: BuildInfo) -> str | None:
    return short_product_version(
        info.exe_product_version or info.product_version
    )


def live_ruleta_major_minor_for_target(scan_target: str) -> str | None:
    try:
        root = scan_target_path(scan_target)
    except (OSError, ValueError):
        return None
    info = detect_roulette_exe_version(root)
    return short_product_version(
        info.product_version or info.display_version or info.file_version
    )


def live_ruleta_exe_version_for_target(scan_target: str) -> str | None:
    """Full PE ProductVersion of live Ruleta.exe (e.g. 10.1.8.0 / 10.2.0.876)."""
    try:
        root = scan_target_path(scan_target)
    except (OSError, ValueError):
        return None
    try:
        info = detect_roulette_exe_version(root)
    except OSError:
        return None
    return (
        (info.product_version or info.display_version or info.file_version or "")
        .strip()
        or None
    )


def format_live_ruleta_sw_banner(version: str | None) -> str:
    """Toolbar text showing which Ruleta.exe is on the scan target."""
    ver = (version or "").strip()
    if not ver:
        return "Running: Ruleta.exe unknown"
    return f"Running: Ruleta.exe {ver}"


def software_version_mismatch_warning(
    snapshot_info: BuildInfo,
    scan_target: str,
) -> str | None:
    """Warn when restoring roulette config onto a different Ruleta major.minor."""
    profile = (snapshot_info.profile_id or "").strip().casefold()
    if profile.startswith("slot"):
        return None
    snap = snapshot_ruleta_major_minor(snapshot_info)
    try:
        live = live_ruleta_major_minor_for_target(scan_target)
    except OSError:
        return None
    extra = ""
    try:
        from config_scanner.paytable_compat import (
            live_exe_is_ruleta_10_2,
            signed_device_manager_10_2_paytable_ids,
        )

        root = scan_target_path(scan_target)
        sas_ids = signed_device_manager_10_2_paytable_ids(root)
        if sas_ids and not live_exe_is_ruleta_10_2(live):
            extra = (
                " Signed DeviceManager still has "
                + ", ".join(sas_ids)
                + " — Ruleta 10.1 will crash (bad conversion) unless you swap "
                "to 10.2 or re-sign that file. Config remap cannot change the MAC."
            )
    except (OSError, ValueError, TypeError):
        extra = ""
    hint = restore_scope_hint(snapshot_info)
    trial = ""
    if snap == "10.2":
        trial = (
            " Snapshot is Ruleta 10.2 Development — that exe shows ERROR 30 "
            "(trial / LLAVE) even with a valid 37A55022 licence XML. "
            "Revert must push the 10.1 Ruleta pack, not config alone."
        )
    if (not snap or not live or snap == live) and not extra:
        return f"{trial.strip()}{hint}" if trial else None
    if not snap or not live or snap == live:
        body = " ".join(part for part in (extra.strip(), trial.strip()) if part)
        return f"{body}{hint}" if body else None
    return (
        f"Software version mismatch: snapshot is Ruleta {snap}, "
        f"live Ruleta.exe is {live}. Restore still writes config; 10.2-only "
        "paytable names in combo/AurumSetup are remapped."
        + extra
        + (f" {trial.strip()}" if trial else "")
        + hint
    )


def cabinet_host_for_scan_target(scan_target: str) -> str:
    """UNC host, or ``local`` for a path on this machine."""
    text = (scan_target or "").replace("/", "\\").strip()
    if text.startswith("\\\\"):
        host = text.lstrip("\\").split("\\", 1)[0].strip()
        return host or "local"
    return "local"


_FOUR_PART_VERSION_RE = re.compile(r"\d+\.\d+\.\d+\.\d+")


def software_versions_search_dirs(
    scan_target: str | None = None,
    tool_root: Path | None = None,
) -> list[Path]:
    """Pack folders: tool root, install root, then the cabinet ConfigScanner share."""
    from network.software_version_swap import software_versions_dir

    seen: set[str] = set()
    out: list[Path] = []

    def _add(path: Path) -> None:
        key = str(path).replace("/", "\\").casefold().rstrip("\\")
        if not key or key in seen:
            return
        seen.add(key)
        out.append(path)

    if tool_root is not None:
        _add(Path(tool_root) / "software_versions")
    _add(software_versions_dir())
    host = cabinet_host_for_scan_target(scan_target or "")
    if host and host != "local":
        _add(Path(rf"\\{host}\ConfigScanner\software_versions"))
        _add(Path(rf"\\{host}\USB\ConfigScanner\software_versions"))
        _add(Path(rf"\\{host}\USB_Remote\ConfigScanner\software_versions"))
    return out


def resolve_software_versions_dir(
    scan_target: str | None = None,
    tool_root: Path | None = None,
    versions_dir: Path | None = None,
) -> Path:
    from network.software_version_swap import software_versions_dir

    if versions_dir is not None:
        return Path(versions_dir)
    for candidate in software_versions_search_dirs(scan_target, tool_root):
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return software_versions_dir()


def snapshot_embedded_software_dir(snapshot_dir: Path | None) -> Path | None:
    """Complete ``software/`` tree inside a snapshot, or None."""
    if snapshot_dir is None:
        return None
    root = Path(snapshot_dir) / SNAPSHOT_SOFTWARE_SUBDIR
    try:
        from network.software_version_swap import preflight_source

        if root.is_dir() and not preflight_source(root):
            return root
    except OSError:
        return None
    return None


def snapshot_has_embedded_software(snapshot_dir: Path | None) -> bool:
    """Fast list badge: Ruleta.exe under ``software/`` (full pack check is on restore)."""
    if snapshot_dir is None:
        return False
    try:
        return (Path(snapshot_dir) / SNAPSHOT_SOFTWARE_SUBDIR / "Ruleta.exe").is_file()
    except OSError:
        return False


def capture_ruleta_software_into_snapshot(
    scan_target: str,
    snapshot_dir: Path,
) -> SoftwareCaptureResult:
    """Copy the 7 surgical Ruleta files plus 64-bit OpenSSL into ``software/``.

    Does not copy ``licence.dll`` or trial persistent files. 32-bit
    ``services\\ntp\\bin\\libeay32.dll`` is ignored.
    """
    from network.pe_runtime import (
        harvest_local_runtime,
        harvest_managed_declarations,
        runtime_refuse_reason,
    )
    from network.software_version_swap import (
        SOFTWARE_VERSION_FILES,
        harvest_x64_openssl_runtime,
        preflight_source,
    )

    dest = Path(snapshot_dir) / SNAPSHOT_SOFTWARE_SUBDIR
    try:
        live = scan_target_path(scan_target) / "ruleta"
    except (OSError, ValueError) as exc:
        return SoftwareCaptureResult(
            False, 0, (), None, f"Cannot resolve live ruleta: {exc}"
        )
    try:
        if not live.is_dir():
            return SoftwareCaptureResult(
                False,
                0,
                (),
                None,
                "No live ruleta folder — config was saved, software was not captured.",
            )
        missing_live = tuple(preflight_source(live))
    except OSError as exc:
        return SoftwareCaptureResult(False, 0, (), None, str(exc))

    copied = 0
    dest.mkdir(parents=True, exist_ok=True)
    for _, rel in SOFTWARE_VERSION_FILES:
        src = live / rel
        if not src.is_file():
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
        copied += 1
    harvested = harvest_x64_openssl_runtime(live, dest)
    for name in harvest_local_runtime(live, dest):
        if name not in harvested:
            harvested.append(name)
    managed = harvest_managed_declarations(dest, extra_roots=(live, live / "lib"))
    if managed:
        harvested.append("GoldClub.ManagedDeclarations.dll")
    copied += len(harvested)
    still = tuple(preflight_source(dest))
    if still:
        return SoftwareCaptureResult(
            False,
            copied,
            still,
            dest,
            "Software capture incomplete (missing: "
            + ", ".join(still)
            + "). Restore will use a matching software_versions pack if one exists.",
        )
    extra = ""
    if missing_live:
        extra = " (live tree was incomplete before copy)"
    if harvested:
        extra += " Includes 64-bit " + ", ".join(harvested) + "."
    else:
        runtime_missing = runtime_refuse_reason(dest)
        if runtime_missing:
            extra += " " + runtime_missing
    return SoftwareCaptureResult(
        True,
        copied,
        (),
        dest,
        f"Captured {copied} Ruleta software files into the snapshot.{extra}",
    )


def resolve_software_pack_for_snapshot(
    snapshot_info: BuildInfo,
    scan_target: str,
    *,
    snapshot_dir: Path | None = None,
    versions_dir: Path | None = None,
    tool_root: Path | None = None,
) -> Path | None:
    """Prefer the pack stored in the snapshot, then software_versions."""
    embedded = snapshot_embedded_software_dir(snapshot_dir)
    if embedded is not None:
        return embedded
    resolved_dir = resolve_software_versions_dir(
        scan_target, tool_root, versions_dir
    )
    return find_ruleta_software_pack(snapshot_info, resolved_dir)


def find_ruleta_software_pack(
    snapshot_info: BuildInfo,
    versions_dir: Path | None = None,
) -> Path | None:
    """Newest complete pack whose name contains the snapshot Ruleta version.

    A four-part ProductVersion (``10.2.0.827``) must match that patch. Do not
    silently push ``10.2.0.684`` for a 827 snapshot — that is the ERROR 30
    Development trial build.
    """
    from network.software_version_swap import list_version_packages, software_versions_dir

    packs = list_version_packages(versions_dir or software_versions_dir())
    if not packs:
        return None
    full = (
        snapshot_info.exe_product_version or snapshot_info.product_version or ""
    ).strip()
    if full:
        exact = [p for p in packs if full in p.name]
        if exact:
            return exact[0]
        if _FOUR_PART_VERSION_RE.search(full):
            return None
    mm = snapshot_ruleta_major_minor(snapshot_info)
    if not mm:
        return None
    candidates: list[Path] = []
    for pack in packs:
        if mm not in pack.name:
            continue
        if "10.2.0.0" in pack.name and full != "10.2.0.0":
            continue
        candidates.append(pack)
    return candidates[0] if candidates else None


def restore_scope_hint(snapshot_info: BuildInfo) -> str:
    """Short note when a snapshot is not a self-contained full backup."""
    pack = find_ruleta_software_pack(snapshot_info)
    if pack is not None:
        return (
            f" Restore this snapshot to push config plus Ruleta binaries "
            f"(uses {pack.name} unless the snapshot already has software/)."
        )
    return (
        " This snapshot has no embedded software. Create a full snapshot "
        "to capture config + Ruleta binaries together."
    )


def push_matching_ruleta_software(
    snapshot_info: BuildInfo,
    scan_target: str,
    *,
    snapshot_dir: Path | None = None,
    versions_dir: Path | None = None,
    tool_root: Path | None = None,
    clear_trial_tokens: bool = True,
) -> str:
    """Copy the matching surgical Ruleta pack onto the live dest. Returns a note."""
    from network.software_version_swap import run_swap

    pack = resolve_software_pack_for_snapshot(
        snapshot_info,
        scan_target,
        snapshot_dir=snapshot_dir,
        versions_dir=versions_dir,
        tool_root=tool_root,
    )
    ver = (
        snapshot_info.exe_product_version or snapshot_info.product_version or "?"
    )
    if pack is None:
        raise SoftwarePushError(
            f"No Ruleta software pack for {ver}. "
            "Create a full snapshot (config + software), or add a pack under "
            "software_versions."
        )
    dest = scan_target_path(scan_target) / "ruleta"
    refused = pack_refuse_reason(pack)
    if refused:
        raise SoftwarePushError(refused)
    from network.software_version_swap import openssl_runtime_refuse_reason

    openssl = openssl_runtime_refuse_reason(pack, dest)
    if openssl:
        raise SoftwarePushError(openssl)
    host = cabinet_host_for_scan_target(scan_target)
    result = run_swap(
        host,
        pack,
        dest_unc=dest,
        skip_launch=True,
        wait_for_game=False,
        clear_trial_tokens=clear_trial_tokens,
    )
    if result.ok:
        note = f"Pushed Ruleta software from {pack.name} to {dest}."
        if clear_trial_tokens:
            note += (
                " Stale LLAVE trial tokens were cleared with the push (same as "
                "Push-Ruleta102876). Embedded snapshot trial bind is restored "
                "after config write when available."
            )
        else:
            note += " LLAVE trial bind was preserved (revert undo)."
        return note
    raise SoftwarePushError(f"Software push from {pack.name} failed: {result.log}")


def import_ruleta_binaries_keep_profile(
    snapshot_info: BuildInfo,
    scan_target: str,
    *,
    snapshot_dir: Path | None = None,
    versions_dir: Path | None = None,
    tool_root: Path | None = None,
    clear_trial_tokens: bool = True,
) -> list[str]:
    """Push the matching pack without replacing this cabinet's HW/setup/SAS.

    Freezes live setup/godot/switches first, refuses trial / silent-exit packs,
    copies the surgical 7-file set, then reapplies the frozen files if the
    dest setup changed. Licence.dll is skipped when it already exists.
    """
    from config_scanner.cabinet_profile import (
        donor_refuse_paths_present,
        freeze_cabinet_profile,
        reapply_frozen_cabinet_files,
    )
    from network.software_version_swap import run_swap

    dest_root = scan_target_path(scan_target)
    notes: list[str] = []
    profile = freeze_cabinet_profile(
        dest_root,
        tool_root=tool_root,
        scan_target=scan_target,
        serial=snapshot_info.machine_serial,
    )
    written = profile.get("writtenTo") or []
    if written:
        notes.append(f"froze cabinet profile to {written[0]}")
    hw = profile.get("hwLeaves")
    if isinstance(hw, dict) and hw:
        bits = [f"{key}={value}" for key, value in hw.items()]
        notes.append("cabinet HW leaves: " + ", ".join(bits))

    pack = resolve_software_pack_for_snapshot(
        snapshot_info,
        scan_target,
        snapshot_dir=snapshot_dir,
        versions_dir=versions_dir,
        tool_root=tool_root,
    )
    ver = (
        snapshot_info.exe_product_version or snapshot_info.product_version or "?"
    )
    if pack is None:
        raise SoftwarePushError(
            f"No Ruleta software pack for {ver}. "
            "Create a full snapshot, or restore config only."
        )
    refused = pack_refuse_reason(pack)
    if refused:
        raise SoftwarePushError(refused)
    from network.software_version_swap import openssl_runtime_refuse_reason

    dest = dest_root / "ruleta"
    openssl = openssl_runtime_refuse_reason(pack, dest)
    if openssl:
        raise SoftwarePushError(openssl)
    extra = donor_refuse_paths_present(pack)
    if extra:
        notes.append(
            "donor pack also has "
            + ", ".join(extra)
            + " — not copied (surgical binaries only)"
        )

    dest = dest_root / "ruleta"
    host = cabinet_host_for_scan_target(scan_target)
    result = run_swap(
        host,
        pack,
        dest_unc=dest,
        skip_launch=True,
        wait_for_game=False,
        clear_trial_tokens=clear_trial_tokens,
    )
    if not result.ok:
        reapply_frozen_cabinet_files(
            dest_root, tool_root=tool_root, serial=snapshot_info.machine_serial
        )
        raise SoftwarePushError(f"Software push from {pack.name} failed: {result.log}")
    notes.append(f"Pushed Ruleta binaries from {pack.name} to {dest}.")
    notes.extend(
        reapply_frozen_cabinet_files(
            dest_root, tool_root=tool_root, serial=snapshot_info.machine_serial
        )
    )
    return notes
