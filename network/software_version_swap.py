"""Surgical roulette software version swap (Frontend / Middleware / Backend).

On start (cabinet/USB): auto-fetch live C:\\goldclub\\ruleta into
software_versions\\<Ruleta_v…_build…> with godot\\/lib\\ hierarchy.
Skip if that version folder already exists; a different live version creates
a new folder. Apply: Kill-All -> copy package -> Run-FullStack (local or WinRM).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from automation.remote_exec import (
    ensure_lab_winrm_trusted_hosts,
    winrm_run_elevated_script,
    winrm_run_script,
)
from network.lab_access import (
    LabCredentialError,
    assert_ruleta_dest_unc,
    ensure_lab_smb_credential,
    require_lab_fleet_ip,
)

logger = logging.getLogger(__name__)

# Relative to ruleta\ root
SOFTWARE_VERSION_FILES: tuple[tuple[str, str], ...] = (
    ("Frontend", r"godot\RouletteGui.pck"),
    ("Frontend", r"godot\.mono\assemblies\RouletteWebApiModels.dll"),
    ("Frontend", r"godot\.mono\assemblies\RouletteGui2.dll"),
    ("Middleware", r"lib\RouletteWebApiModels.dll"),
    ("Middleware", r"lib\GoldClub.ManagedRendererWebApiServer.dll"),
    ("Middleware", r"lib\GoldClub.ManagedRendererWebApiProxy.dll"),
    ("Backend", r"Ruleta.exe"),
)

# Same WIBU dongle; copy the CodeMeter runtime when the pack has it.
OPTIONAL_SOFTWARE_VERSION_FILES: tuple[tuple[str, str], ...] = (
    ("Licence", r"licence.dll"),
    # 10.2.0.876 Ruleta.exe imports these; 10.1.8.0 does not. NTP's
    # services\ntp\bin\libeay32.dll is 32-bit and will not load into x64 Ruleta.
    ("OpenSSL", r"libeay32.dll"),
    ("OpenSSL", r"ssleay32.dll"),
    # 876 WebApiProxy needs 2.0.9725.*; 10.1 leftovers TypeLoadException.
    ("Middleware", r"lib\GoldClub.ManagedDeclarations.dll"),
)

# Flat drop next to LogInvestigator.exe: basename -> relative dest(s).
# RouletteWebApiModels.dll is shipped to both Frontend and Middleware trees.
FLAT_BASENAME_TARGETS: dict[str, tuple[str, ...]] = {
    "RouletteGui.pck": (r"godot\RouletteGui.pck",),
    "RouletteGui2.dll": (r"godot\.mono\assemblies\RouletteGui2.dll",),
    "RouletteWebApiModels.dll": (
        r"godot\.mono\assemblies\RouletteWebApiModels.dll",
        r"lib\RouletteWebApiModels.dll",
    ),
    "GoldClub.ManagedRendererWebApiServer.dll": (
        r"lib\GoldClub.ManagedRendererWebApiServer.dll",
    ),
    "GoldClub.ManagedRendererWebApiProxy.dll": (
        r"lib\GoldClub.ManagedRendererWebApiProxy.dll",
    ),
    "Ruleta.exe": (r"Ruleta.exe",),
}

# Optional flat OpenSSL next to the exe. Only 64-bit PE is ingested.
OPTIONAL_FLAT_BASENAME_TARGETS: dict[str, tuple[str, ...]] = {
    "libeay32.dll": (r"libeay32.dll",),
    "ssleay32.dll": (r"ssleay32.dll",),
}

ProgressCb = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class SwapResult:
    ok: bool
    log: str


@dataclass(frozen=True, slots=True)
class VersionPackageFetch:
    """Result of fetching a live ruleta install into ``software_versions``."""

    path: Path
    skipped_existing: bool  # True when an identical version folder was reused


def copy_software_version_files(source_ruleta: Path) -> tuple[tuple[str, str], ...]:
    """Required 7 files plus optional licence / 64-bit OpenSSL when present."""
    root = Path(source_ruleta)
    extra: list[tuple[str, str]] = []
    seen: set[str] = {rel.casefold() for _, rel in SOFTWARE_VERSION_FILES}
    for pair in OPTIONAL_SOFTWARE_VERSION_FILES:
        path = root / pair[1]
        if not path.is_file():
            continue
        if pair[0] == "OpenSSL" and pe_machine(path) != _PE_AMD64:
            continue
        extra.append(pair)
        seen.add(pair[1].casefold())
    try:
        from network.pe_runtime import is_local_runtime_dll

        for path in root.glob("*.dll"):
            key = path.name.casefold()
            if key in seen or not is_local_runtime_dll(path.name):
                continue
            if pe_machine(path) != _PE_AMD64:
                continue
            extra.append(("Runtime", path.name))
            seen.add(key)
    except OSError:
        pass
    return SOFTWARE_VERSION_FILES + tuple(extra)


def software_version_rel_paths() -> tuple[str, ...]:
    return tuple(rel for _, rel in SOFTWARE_VERSION_FILES)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _roulette_tools_dir() -> Path:
    """Resolve roulette scripts: EXE install folder first, then frozen bundle, then repo."""
    probe = ("Kill-All.ps1", "Run-FullStack.ps1", "GoldClubServices.ps1")
    candidates: list[Path] = []
    root = app_install_root()
    candidates.extend(
        [
            root / "scripts" / "roulette",
            root / "cabinet_tools" / "roulette",
            root,
        ]
    )
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        candidates.extend(
            [
                meipass / "cabinet_tools" / "roulette",
                meipass / "scripts" / "roulette",
            ]
        )
    candidates.append(_repo_root() / "cabinet_tools" / "roulette")

    for d in candidates:
        try:
            if all((d / name).is_file() for name in probe):
                return d
        except OSError:
            continue
    for d in candidates:
        try:
            if d.is_dir():
                return d
        except OSError:
            continue
    return _repo_root() / "cabinet_tools" / "roulette"


def app_install_root() -> Path:
    """Folder that holds LogInvestigator.exe (USB/cabinet) or repo root in dev."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    try:
        from config_scanner.paths import tool_root

        return tool_root()
    except Exception:
        return _repo_root()


def software_versions_dir(install_root: Path | None = None) -> Path:
    """Versioned packages live here for hand copy-paste upgrades."""
    root = Path(install_root) if install_root else app_install_root()
    return root / "software_versions"


def local_ruleta_dest() -> Path:
    for candidate in (Path(r"C:\goldclub\ruleta"), Path(r"C:\Goldclub\ruleta")):
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return Path(r"C:\goldclub\ruleta")


def is_local_cabinet_host() -> bool:
    """True when this machine has a GoldClub ruleta install (run-on-cabinet)."""
    try:
        return local_ruleta_dest().is_dir()
    except OSError:
        return False


def dest_ruleta_unc(ip: str) -> Path:
    host = require_lab_fleet_ip(ip)
    return Path(rf"\\{host}\c$\goldclub\ruleta")


def is_unc_path(path: Path | str) -> bool:
    text = str(path).replace("/", "\\")
    return text.startswith("\\\\")


def preflight_source(source_ruleta: Path) -> list[str]:
    """Return relative paths missing under ``source_ruleta``."""
    root = Path(source_ruleta)
    missing: list[str] = []
    for _, rel in SOFTWARE_VERSION_FILES:
        if not (root / rel).is_file():
            missing.append(rel)
    return missing


_PE_AMD64 = 0x8664
_LIBEAY_NAMES = (b"LIBEAY32.dll", b"LIBEAY32.DLL", b"libeay32.dll")


def pe_machine(path: Path) -> int | None:
    """COFF Machine field, or None when the file is not a readable PE."""
    try:
        data = Path(path).read_bytes()[:4096]
    except OSError:
        return None
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    e_lfanew = int.from_bytes(data[0x3C:0x40], "little")
    if e_lfanew + 6 > len(data):
        try:
            data = Path(path).read_bytes()[: e_lfanew + 8]
        except OSError:
            return None
    if e_lfanew + 6 > len(data):
        return None
    if data[e_lfanew : e_lfanew + 4] != b"PE\x00\x00":
        return None
    return int.from_bytes(data[e_lfanew + 4 : e_lfanew + 6], "little")


def ruleta_exe_needs_libeay32(ruleta_exe: Path) -> bool:
    """True when Ruleta.exe's import table names LIBEAY32.dll."""
    try:
        data = Path(ruleta_exe).read_bytes()
    except OSError:
        return False
    return any(name in data for name in _LIBEAY_NAMES)


def openssl_runtime_refuse_reason(
    source_ruleta: Path,
    dest_ruleta: Path | None = None,
) -> str | None:
    """Refuse a push that would start Ruleta without a named local sidecar."""
    from network.pe_runtime import runtime_refuse_reason

    return runtime_refuse_reason(source_ruleta, dest_ruleta)


_OPENSSL_RUNTIME_NAMES = ("libeay32.dll", "ssleay32.dll")


def openssl_search_roots(ruleta_root: Path) -> tuple[Path, ...]:
    """Places a 64-bit OpenSSL runtime may sit (never require NTP's x86 copy)."""
    live = Path(ruleta_root)
    gold = live.parent
    return (
        live,
        live / "lib",
        gold / "bin",
        gold / "lib",
        gold / "common",
        gold / "apps",
    )


def mounted_vhd_ruleta_roots() -> tuple[Path, ...]:
    """Mounted goldclub.vhd interiors (lab: R: GOLDCLUB from E:\\goldclub.vhd)."""
    found: list[Path] = []
    for letter in ("R", "P"):
        for rel in ("ruleta", "goldclub/ruleta", "Goldclub/ruleta"):
            root = Path(f"{letter}:/") / rel
            try:
                if (root / "libeay32.dll").is_file() or (root / "Ruleta.exe").is_file():
                    found.append(root)
            except OSError:
                continue
    return tuple(found)


def find_x64_openssl_file(
    ruleta_root: Path,
    name: str,
    extra_roots: tuple[Path, ...] = (),
) -> Path | None:
    """First 64-bit ``name`` under the live tree. 32-bit files are skipped."""
    for root in (*openssl_search_roots(ruleta_root), *extra_roots):
        candidate = Path(root) / name
        try:
            if candidate.is_file() and pe_machine(candidate) == _PE_AMD64:
                return candidate
        except OSError:
            continue
    return None


def harvest_x64_openssl_runtime(ruleta_root: Path, dest_ruleta: Path) -> list[str]:
    """Copy 64-bit libeay32/ssleay32 next to dest Ruleta.exe. Skip 32-bit NTP.

    When the dest or live Ruleta.exe imports LIBEAY32 and the live tree has no
    x64 copy, also search a mounted 10.2 goldclub.vhd (R:\\ruleta).
    """
    dest = Path(dest_ruleta)
    dest.mkdir(parents=True, exist_ok=True)
    extra: tuple[Path, ...] = ()
    dest_exe = dest / "Ruleta.exe"
    live_exe = Path(ruleta_root) / "Ruleta.exe"
    needs = (
        dest_exe.is_file()
        and ruleta_exe_needs_libeay32(dest_exe)
        or live_exe.is_file()
        and ruleta_exe_needs_libeay32(live_exe)
    )
    if needs:
        extra = mounted_vhd_ruleta_roots()
    copied: list[str] = []
    for name in _OPENSSL_RUNTIME_NAMES:
        src = find_x64_openssl_file(ruleta_root, name, extra_roots=extra)
        if src is None:
            continue
        out = dest / name
        try:
            if src.resolve() != out.resolve():
                shutil.copy2(src, out)
        except OSError:
            shutil.copy2(src, out)
        copied.append(name)
    return copied


def _safe_folder_token(text: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", (text or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or "package"


def package_name_from_ruleta_exe(ruleta_exe: Path) -> str:
    """Build folder name like ``Ruleta_v10.2.0.684_build40097``."""
    from config_scanner.build_version import _extract_version_from_exe

    info = _extract_version_from_exe(Path(ruleta_exe))
    product = _safe_folder_token((info.product_name or "Ruleta").replace(" Module", ""))
    ver = (info.display_version or info.file_version or info.product_version or "unknown").strip()
    ver = ver.replace(" ", "")
    if ver and not ver.lower().startswith("v") and ver[0].isdigit():
        ver = f"v{ver}"
    ver = _safe_folder_token(ver)

    build = ""
    for bv in (
        Path(ruleta_exe).parent / "BuildVersion.txt",
        Path(ruleta_exe).parent / "ruleta" / "BuildVersion.txt",
        app_install_root() / "BuildVersion.txt",
    ):
        if not bv.is_file():
            continue
        try:
            text = bv.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        match = re.search(r"(?im)^\s*Build\s+Number\s*:\s*(\d+)\s*$", text)
        if match:
            build = match.group(1)
            break
    if not build and info.file_version and info.file_version.count(".") >= 3:
        build = info.file_version.rsplit(".", 1)[-1]

    name = f"{product}_{ver}"
    if build:
        name = f"{name}_build{build}"
    return name


def find_flat_drop_files(drop_root: Path) -> dict[str, Path]:
    """Map basename -> path for surgical binaries lying flat under ``drop_root``."""
    root = Path(drop_root)
    found: dict[str, Path] = {}
    if not root.is_dir():
        return found
    try:
        for entry in root.iterdir():
            if not entry.is_file():
                continue
            key = entry.name
            # Case-insensitive match to declared basenames
            for want in (*FLAT_BASENAME_TARGETS, *OPTIONAL_FLAT_BASENAME_TARGETS):
                if key.casefold() == want.casefold():
                    found[want] = entry
                    break
    except OSError:
        return {}
    return found


def flat_drop_is_complete(found: dict[str, Path]) -> bool:
    return all(name in found for name in FLAT_BASENAME_TARGETS)


def ingest_flat_drop(
    drop_root: Path | None = None,
    *,
    versions_dir: Path | None = None,
    progress: ProgressCb | None = None,
    move: bool = False,
) -> Path | None:
    """
    Pull flat binaries from the exe folder into
    ``software_versions\\<Ruleta_v…>\\`` with the ruleta-relative hierarchy.

    Returns the package directory, or None if nothing to ingest.
    """
    lines: list[str] = []
    root = Path(drop_root) if drop_root else app_install_root()
    out_root = Path(versions_dir) if versions_dir else software_versions_dir(root)
    found = find_flat_drop_files(root)
    if not flat_drop_is_complete(found):
        missing = [n for n in FLAT_BASENAME_TARGETS if n not in found]
        if found:
            _emit(progress, f"Flat drop incomplete (missing: {', '.join(missing)})", lines)
        return None

    ruleta_exe = found["Ruleta.exe"]
    pkg_name = package_name_from_ruleta_exe(ruleta_exe)
    pkg_dir = out_root / pkg_name
    _emit(progress, f"Packaging flat drop -> {pkg_dir}", lines)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    for basename, rels in FLAT_BASENAME_TARGETS.items():
        src = found[basename]
        for rel in rels:
            dst = pkg_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if move and len(rels) == 1:
                if dst.exists():
                    dst.unlink()
                shutil.move(str(src), str(dst))
            else:
                shutil.copy2(src, dst)
            _emit(progress, f"  {basename} -> {rel}", lines)
        if move and len(rels) > 1 and src.is_file():
            try:
                src.unlink()
            except OSError:
                pass
    for basename, rels in OPTIONAL_FLAT_BASENAME_TARGETS.items():
        src = found.get(basename)
        if src is None:
            continue
        if pe_machine(src) != _PE_AMD64:
            _emit(progress, f"  skip {basename} (not 64-bit PE)", lines)
            continue
        for rel in rels:
            dst = pkg_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            _emit(progress, f"  {basename} -> {rel} (64-bit)", lines)

    missing = preflight_source(pkg_dir)
    if missing:
        raise RuntimeError("Package incomplete after ingest: " + ", ".join(missing))
    _emit(progress, f"Package ready: {pkg_dir}", lines)
    return pkg_dir


def list_version_packages(versions_dir: Path | None = None) -> list[Path]:
    """Complete packages under software_versions, newest first."""
    root = Path(versions_dir) if versions_dir else software_versions_dir()
    if not root.is_dir():
        return []
    packages: list[Path] = []
    try:
        for child in root.iterdir():
            if child.is_dir() and not preflight_source(child):
                packages.append(child)
    except OSError:
        return []
    packages.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return packages


def find_live_ruleta_root(ip: str | None = None) -> Path | None:
    """Installed ruleta tree to snapshot from (local cabinet or remote UNC)."""
    host = (ip or "").strip().lower()
    if host and host not in {"local", "localhost", "127.0.0.1", "."}:
        unc = dest_ruleta_unc(ip.strip())
        try:
            if unc.is_dir() and (unc / "Ruleta.exe").is_file():
                return unc
        except OSError:
            pass
    live = local_ruleta_dest()
    try:
        if live.is_dir() and (live / "Ruleta.exe").is_file():
            return live
    except OSError:
        pass
    return None


def _ruleta_exe_in(root: Path) -> Path | None:
    for name in ("Ruleta.exe", "ruleta.exe"):
        p = root / name
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def allocate_version_backup_dir(versions_dir: Path, base_name: str) -> Path:
    """Next free backup folder: ``<base>_v2``, ``<base>_v3``, …"""
    root = Path(versions_dir)
    name = (base_name or "").strip() or "package"
    n = 2
    while True:
        candidate = root / f"{name}_v{n}"
        if not candidate.exists():
            return candidate
        n += 1
        if n > 999:
            raise RuntimeError(f"Too many backups for {name!r} under {root}")


def snapshot_live_ruleta_package(
    live_root: Path,
    *,
    install_root: Path | None = None,
    progress: ProgressCb | None = None,
    force_new: bool = False,
) -> VersionPackageFetch | None:
    """
    Copy the 7 surgical binaries from an installed ruleta tree into
    ``<exe>\\software_versions\\<Ruleta_v…_build…>\\`` with correct hierarchy.

    If that version folder already exists and is complete, skip (return it)
    unless ``force_new`` is set — then write ``…_v2`` / ``…_v3`` / …
    A different live version creates a new sibling folder; older packages stay.
    """
    live = Path(live_root)
    missing = preflight_source(live)
    if missing:
        if progress:
            progress("Live ruleta incomplete (missing: " + ", ".join(missing) + ")")
        return None

    exe = _ruleta_exe_in(live)
    if not exe:
        return None

    root = Path(install_root) if install_root else app_install_root()
    versions = software_versions_dir(root)
    pkg_name = package_name_from_ruleta_exe(exe)
    pkg_dir = versions / pkg_name

    if pkg_dir.is_dir() and not preflight_source(pkg_dir):
        if not force_new:
            if progress:
                progress(f"Package already present — skip fetch: {pkg_dir}")
            return VersionPackageFetch(path=pkg_dir, skipped_existing=True)
        pkg_dir = allocate_version_backup_dir(versions, pkg_name)
        if progress:
            progress(f"Creating additional backup: {pkg_dir}")

    if progress:
        progress(f"Fetching live install {live} -> {pkg_dir}")
    versions.mkdir(parents=True, exist_ok=True)
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    for _, rel in copy_software_version_files(live):
        sp = live / rel
        if not sp.is_file():
            continue
        dp = pkg_dir / rel
        dp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sp, dp)
        if progress:
            progress(f"  {rel}")
    for rel in harvest_x64_openssl_runtime(live, pkg_dir):
        if progress:
            progress(f"  {rel} (64-bit)")
    from network.pe_runtime import harvest_local_runtime

    for rel in harvest_local_runtime(live, pkg_dir):
        if progress:
            progress(f"  {rel} (runtime)")

    still_missing = preflight_source(pkg_dir)
    if still_missing:
        raise RuntimeError("Package incomplete after fetch: " + ", ".join(still_missing))
    if progress:
        progress(f"Package ready: {pkg_dir}")
    return VersionPackageFetch(path=pkg_dir, skipped_existing=False)


def ensure_local_version_package(
    *,
    install_root: Path | None = None,
    progress: ProgressCb | None = None,
    ip: str | None = None,
    force_new: bool = False,
) -> VersionPackageFetch | None:
    """
    Auto-fetch the currently installed ruleta version into software_versions\\
    next to LogInvestigator.exe (skip if that version folder already exists).

    Different live software/version => new folder; existing folders are left alone.
    Pass ``force_new=True`` to create ``…_v2`` / ``…_v3`` when the base exists.
    """
    root = Path(install_root) if install_root else app_install_root()
    live = find_live_ruleta_root(ip)
    if live is not None:
        return snapshot_live_ruleta_package(
            live,
            install_root=root,
            progress=progress,
            force_new=force_new,
        )

    if progress:
        progress("No live ruleta install found to fetch (C:\\goldclub\\ruleta).")
    return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _emit(cb: ProgressCb | None, msg: str, lines: list[str]) -> None:
    lines.append(msg)
    if cb:
        cb(msg)


def _stage_roulette_scripts(ip: str, lines: list[str], progress: ProgressCb | None) -> str:
    """Copy Kill-All / Run-FullStack / Invoke-SoftwareVersionSwap to cabinet temp; return remote dir."""
    local_dir = _roulette_tools_dir()
    remote_dir = rf"C:\Windows\Temp\wd_software_swap"
    unc_dir = Path(rf"\\{ip}\c$\Windows\Temp\wd_software_swap")
    unc_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "Kill-All.ps1",
        "Run-FullStack.ps1",
        "Invoke-SoftwareVersionSwap.ps1",
        "GoldClubServices.ps1",
    ):
        src = local_dir / name
        if not src.is_file():
            raise FileNotFoundError(f"Missing local script: {src}")
        dst = unc_dir / name
        shutil.copy2(src, dst)
        _emit(progress, f"Staged {name} -> {remote_dir}\\{name}", lines)
    return remote_dir


def _winrm_run_file(
    *,
    ip: str,
    remote_path: str,
    args: list[str],
    timeout: int,
    lines: list[str],
    progress: ProgressCb | None,
    label: str,
) -> None:
    ensure_lab_winrm_trusted_hosts(ip=ip)
    _emit(progress, f"{label}: {remote_path} {' '.join(args)}", lines)
    name = Path(remote_path).name.casefold()
    if name == "kill-all.ps1" or "Kill-All" in label:
        from automation.cabinet_elevate import run_remote_kill_all_elevated

        ok, detail = run_remote_kill_all_elevated(ip, timeout=timeout)
        if detail:
            for ln in detail.splitlines()[-20:]:
                _emit(progress, f"  {ln}", lines)
        if ok:
            return
        if "Kill-All" in label and "exit=1" in detail.casefold():
            _emit(progress, f"WARN: {label} exit=1 (continuing)", lines)
            return
        raise RuntimeError(detail or f"{label} failed")
    use_elevated = name == "run-fullstack.ps1"
    if use_elevated:
        result = winrm_run_elevated_script(
            ip=ip,
            remote_script_path=remote_path,
            script_args=args,
            timeout=timeout,
        )
    else:
        result = winrm_run_script(
            ip=ip,
            remote_script_path=remote_path,
            script_args=args,
            timeout=timeout,
        )
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if out:
        for ln in out.splitlines()[-40:]:
            _emit(progress, f"  {ln}", lines)
    if err:
        for ln in err.splitlines()[-20:]:
            _emit(progress, f"  ERR: {ln}", lines)
    if result.returncode not in (0, None) and result.returncode != 0:
        # Kill-All may return 1 on already-gone races historically; treat as soft if copy can proceed
        if "Kill-All" in label and result.returncode == 1:
            _emit(progress, f"WARN: {label} exit={result.returncode} (continuing)", lines)
            return
        raise RuntimeError(f"{label} failed exit={result.returncode}")


def _ensure_stack_clear_before_copy(
    *,
    host: str | None,
    dest: Path,
    lines: list[str],
    progress: ProgressCb | None,
    label: str,
) -> None:
    """Verify Kill-All actually released swap targets; force-stop once if not."""
    from automation.cabinet_elevate import bootstrap_hint
    from network.ruleta_stack_probe import (
        force_stop_blocking_processes,
        verify_stack_clear_for_swap,
    )

    ok, detail = verify_stack_clear_for_swap(host, dest)
    if ok:
        _emit(progress, f"{label}: stack clear, destination writable", lines)
        return
    _emit(progress, f"WARN: {label} — {detail}; forcing process stop …", lines)
    forced_ok, forced_msg = force_stop_blocking_processes(host)
    hint = bootstrap_hint(host) if host and host != "local" else ""
    if not forced_ok:
        raise RuntimeError(
            f"{label} left processes or locked files ({detail}). "
            f"Force stop failed: {forced_msg}. "
            + (hint or "Stop the GoldClub stack locally, then retry.")
        )
    ok, detail = verify_stack_clear_for_swap(host, dest)
    if not ok:
        raise RuntimeError(
            f"{label} finished but swap targets are still blocked: {detail}. "
            + (hint or "Stop the GoldClub stack locally, then retry.")
        )
    _emit(progress, f"{label}: forced stop OK; destination writable", lines)


def _copy_file_with_retry(
    sp: Path,
    dp: Path,
    *,
    host: str | None,
    progress: ProgressCb | None,
    lines: list[str],
    rel: str,
) -> None:
    from automation.cabinet_elevate import bootstrap_hint
    from network.ruleta_stack_probe import force_stop_blocking_processes

    for attempt in (1, 2):
        try:
            shutil.copy2(sp, dp)
            return
        except OSError as exc:
            errno = getattr(exc, "errno", None)
            text = str(exc).casefold()
            locked = errno == 32 or "being used by another process" in text
            readonly = errno == 19 or "write protected" in text
            if readonly:
                raise RuntimeError(
                    f"Destination is read-only: {dp.parent} ({exc}). "
                    "Use the cabinet IP or UNC share as Scan target."
                ) from exc
            if locked and attempt == 1:
                _emit(
                    progress,
                    f"WARN: {rel} locked ({exc}); force-stopping stack and retrying …",
                    lines,
                )
                forced_ok, forced_msg = force_stop_blocking_processes(host)
                if not forced_ok:
                    raise RuntimeError(
                        f"Cannot copy {rel}: file in use and force stop failed: {forced_msg}"
                    ) from exc
                continue
            if locked:
                hint = bootstrap_hint(host) if host and host != "local" else ""
                raise RuntimeError(
                    f"Cannot copy {rel}: file still in use after force stop ({exc}). "
                    + (hint or "Stop the GoldClub stack locally, then retry.")
                ) from exc
            raise


def _find_local_roulette_script(name: str) -> Path | None:
    """Resolve Kill-All.ps1 / Run-FullStack.ps1 beside the exe or in repo tools."""
    root = app_install_root()
    candidates = (
        root / "scripts" / "roulette" / name,
        root / name,
        root / "cabinet_tools" / "roulette" / name,
        _roulette_tools_dir() / name,
    )
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _run_local_ps1(
    script: Path,
    args: list[str],
    *,
    timeout: int,
    lines: list[str],
    progress: ProgressCb | None,
    label: str,
) -> None:
    _emit(progress, f"{label} (local): {script} {' '.join(args)}", lines)
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": timeout,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *args,
        ],
        **run_kw,
    )
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if out:
        for ln in out.splitlines()[-40:]:
            _emit(progress, f"  {ln}", lines)
    if err:
        for ln in err.splitlines()[-20:]:
            _emit(progress, f"  ERR: {ln}", lines)
    if result.returncode not in (0, None) and result.returncode != 0:
        if "Kill-All" in label and result.returncode == 1:
            _emit(progress, f"WARN: {label} exit={result.returncode} (continuing)", lines)
            return
        raise RuntimeError(f"{label} failed exit={result.returncode}")


def _wait_for_ruleta_local(
    *,
    timeout_s: float = 90.0,
    progress: ProgressCb | None,
    lines: list[str],
) -> bool:
    deadline = time.monotonic() + timeout_s
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": 20,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    while time.monotonic() < deadline:
        r = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "if (Get-Process -Name ruleta,Ruleta -EA SilentlyContinue) { 'RULETA_UP' } else { 'RULETA_DOWN' }",
            ],
            **run_kw,
        )
        if "RULETA_UP" in ((r.stdout or "") + (r.stderr or "")):
            _emit(progress, "ruleta process is up", lines)
            return True
        _emit(progress, "waiting for ruleta …", lines)
        time.sleep(3.0)
    _emit(progress, "WARN: timed out waiting for ruleta (launch may still be in progress)", lines)
    return False


def _wait_for_ruleta(
    ip: str,
    *,
    timeout_s: float = 90.0,
    progress: ProgressCb | None,
    lines: list[str],
) -> bool:
    """Best-effort: Ruleta.exe appears on cabinet via WinRM (CredMan; no -Command password)."""
    host = require_lab_fleet_ip(ip)
    ensure_lab_winrm_trusted_hosts(ip=host)
    unc_dir = Path(rf"\\{host}\c$\Windows\Temp\wd_software_swap")
    try:
        unc_dir.mkdir(parents=True, exist_ok=True)
        probe = unc_dir / "Probe-RuletaUp.ps1"
        probe.write_text(
            "if (Get-Process -Name ruleta,Ruleta -ErrorAction SilentlyContinue) "
            "{ 'RULETA_UP' } else { 'RULETA_DOWN' }\n",
            encoding="utf-8",
        )
    except OSError as e:
        _emit(progress, f"WARN: could not stage ruleta probe: {e}", lines)
        return False
    remote_path = r"C:\Windows\Temp\wd_software_swap\Probe-RuletaUp.ps1"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            result = winrm_run_script(
                ip=host,
                remote_script_path=remote_path,
                timeout=40,
            )
        except Exception as e:  # noqa: BLE001
            _emit(progress, f"waiting for ruleta (winrm error: {e}) …", lines)
            time.sleep(3.0)
            continue
        blob = (result.stdout or "") + (result.stderr or "")
        if "RULETA_UP" in blob:
            _emit(progress, "ruleta process is up", lines)
            return True
        _emit(progress, "waiting for ruleta …", lines)
        time.sleep(3.0)
    _emit(progress, "WARN: timed out waiting for ruleta (launch may still be in progress)", lines)
    return False


def _copy_surgical_files(
    src: Path,
    dest: Path,
    *,
    progress: ProgressCb | None,
    lines: list[str],
    host: str | None = None,
) -> None:
    for layer, rel in copy_software_version_files(src):
        sp = src / rel
        dp = dest / rel
        if rel.lower().endswith("licence.dll") and dp.is_file():
            try:
                if sha256_file(sp) == sha256_file(dp):
                    _emit(progress, f"Skip existing {rel} (same sha256)", lines)
                    continue
            except OSError:
                pass
            _emit(progress, f"Replace {rel} (build-paired licence runtime)", lines)
        _emit(progress, f"Copy [{layer}] {rel} …", lines)
        dp.parent.mkdir(parents=True, exist_ok=True)
        _copy_file_with_retry(
            sp,
            dp,
            host=host,
            progress=progress,
            lines=lines,
            rel=rel,
        )
        sh = sha256_file(sp)
        dh = sha256_file(dp)
        if sh != dh:
            raise RuntimeError(f"Hash mismatch after copy: {rel}")
        _emit(progress, f"OK {rel} sha256={sh[:12]}…", lines)
    from network.pe_runtime import harvest_managed_declarations

    harvested = harvest_managed_declarations(dest, extra_roots=(src, src / "lib"))
    if harvested:
        _emit(progress, harvested, lines)


def run_swap(
    ip: str,
    source_ruleta: Path | str,
    *,
    dest_unc: Path | str | None = None,
    skip_kill: bool = False,
    skip_launch: bool = False,
    dry_run: bool = False,
    progress: ProgressCb | None = None,
    wait_for_game: bool = True,
    clear_trial_tokens: bool = True,
) -> SwapResult:
    """
    Kill game, copy surgical binaries, relaunch.

    Local cabinet: ``dest`` is ``C:\\goldclub\\ruleta`` (non-UNC) — no WinRM.
    Remote: WinRM Kill-All / SMB copy / WinRM Run-FullStack.

    ``source_ruleta`` must contain the 7 relative paths (a ``ruleta`` package root).
    """
    lines: list[str] = []
    host = (ip or "").strip()
    src = Path(source_ruleta)
    if not src.is_dir():
        return SwapResult(False, f"Source ruleta folder not found: {src}")

    missing = preflight_source(src)
    if missing:
        msg = "Missing source files:\n  " + "\n  ".join(missing)
        return SwapResult(False, msg)

    local_mode = False
    if dest_unc:
        dest = Path(dest_unc)
        local_mode = not is_unc_path(dest)
    elif is_local_cabinet_host() and (
        not host or host in {"127.0.0.1", "localhost", "."} or host == "local"
    ):
        dest = local_ruleta_dest()
        local_mode = True
        host = host or "local"
    elif not host:
        if is_local_cabinet_host():
            dest = local_ruleta_dest()
            local_mode = True
            host = "local"
        else:
            return SwapResult(False, "Cabinet IP is required (or run on the cabinet)")
    else:
        dest = dest_ruleta_unc(host)
        local_mode = not is_unc_path(dest)

    if not local_mode:
        try:
            host = require_lab_fleet_ip(host)
            dest = assert_ruleta_dest_unc(host, dest)
        except (ValueError, LabCredentialError) as e:
            return SwapResult(False, str(e))

    _emit(progress, f"Mode:   {'LOCAL (on cabinet)' if local_mode else 'REMOTE (WinRM+SMB)'}", lines)
    _emit(progress, f"Source: {src}", lines)
    _emit(progress, f"Dest:   {dest}", lines)

    if dry_run:
        for layer, rel in copy_software_version_files(src):
            sp = src / rel
            _emit(
                progress,
                f"WhatIf: [{layer}] {rel} ({sp.stat().st_size} bytes) -> {dest / rel}",
                lines,
            )
        _emit(progress, "DONE OK (dry-run)", lines)
        return SwapResult(True, "\n".join(lines))

    try:
        if local_mode:
            from config_scanner.dest_preflight import goldclub_dest_writable

            gc_root = dest.parent if dest.name.casefold() == "ruleta" else dest
            ok, writable_msg = goldclub_dest_writable(gc_root)
            if not ok:
                return SwapResult(
                    False,
                    writable_msg or f"Destination not writable: {dest}",
                )
            if not dest.parent.exists():
                raise FileNotFoundError(f"Destination parent not reachable: {dest.parent}")
            kill_ps1 = _find_local_roulette_script("Kill-All.ps1")
            run_ps1 = _find_local_roulette_script("Run-FullStack.ps1")
            if not skip_kill:
                if not kill_ps1:
                    raise FileNotFoundError(
                        "Kill-All.ps1 not found next to LogInvestigator "
                        "(expected scripts\\roulette\\Kill-All.ps1)"
                    )
                _run_local_ps1(
                    kill_ps1,
                    ["-AlreadyElevated"],
                    timeout=180,
                    lines=lines,
                    progress=progress,
                    label="Kill-All",
                )
            else:
                _emit(progress, "SkipKill", lines)

            _ensure_stack_clear_before_copy(
                host="local",
                dest=dest,
                lines=lines,
                progress=progress,
                label="Kill-All",
            )

            _copy_surgical_files(
                src, dest, progress=progress, lines=lines, host="local"
            )

            if clear_trial_tokens:
                from roulette_trial import (
                    clear_stale_llave_after_software_swap,
                    llave_bind_note_after_clear,
                )

                gc_root = dest.parent if dest.name.casefold() == "ruleta" else dest
                cleared = clear_stale_llave_after_software_swap(gc_root)
                if cleared:
                    note = llave_bind_note_after_clear(cleared)
                    _emit(progress, f"Cleared {len(cleared)} stale LLAVE token(s)", lines)
                    if note:
                        _emit(progress, note, lines)
            else:
                _emit(progress, "SkipClearTrialTokens (revert / preserve bind)", lines)

            if not skip_launch:
                if not run_ps1:
                    raise FileNotFoundError(
                        "Run-FullStack.ps1 not found next to LogInvestigator "
                        "(expected scripts\\roulette\\Run-FullStack.ps1)"
                    )
                _run_local_ps1(
                    run_ps1,
                    ["-AlreadyElevated"],
                    timeout=300,
                    lines=lines,
                    progress=progress,
                    label="Run-FullStack",
                )
                if wait_for_game:
                    _wait_for_ruleta_local(progress=progress, lines=lines)
            else:
                _emit(progress, "SkipLaunch", lines)
        else:
            ensure_lab_smb_credential(host)
            if not dest.parent.exists():
                raise FileNotFoundError(f"Destination parent not reachable: {dest.parent}")

            usb_kill = Path(rf"\\{host}\USB\usb_scripts\roulette\Kill-All.ps1")
            usb_run = Path(rf"\\{host}\USB\usb_scripts\roulette\Run-FullStack.ps1")
            if not (usb_kill.is_file() and usb_run.is_file()):
                usb_kill = Path(rf"\\{host}\USB_Remote\usb_scripts\roulette\Kill-All.ps1")
                usb_run = Path(rf"\\{host}\USB_Remote\usb_scripts\roulette\Run-FullStack.ps1")
            if usb_kill.is_file() and usb_run.is_file():
                kill_ps1 = r"D:\usb_scripts\roulette\Kill-All.ps1"
                run_ps1 = r"D:\usb_scripts\roulette\Run-FullStack.ps1"
                _emit(progress, f"Using USB stack scripts: {kill_ps1}", lines)
            else:
                remote_dir = _stage_roulette_scripts(host, lines, progress)
                kill_ps1 = rf"{remote_dir}\Kill-All.ps1"
                run_ps1 = rf"{remote_dir}\Run-FullStack.ps1"

            if not skip_kill:
                _winrm_run_file(
                    ip=host,
                    remote_path=kill_ps1,
                    args=["-AlreadyElevated"],
                    timeout=180,
                    lines=lines,
                    progress=progress,
                    label="Kill-All",
                )
            else:
                _emit(progress, "SkipKill", lines)

            _ensure_stack_clear_before_copy(
                host=host,
                dest=dest,
                lines=lines,
                progress=progress,
                label="Kill-All",
            )

            _copy_surgical_files(src, dest, progress=progress, lines=lines, host=host)

            if clear_trial_tokens:
                from roulette_trial import (
                    clear_stale_llave_after_software_swap,
                    llave_bind_note_after_clear,
                )

                gc_root = dest.parent if dest.name.casefold() == "ruleta" else dest
                cleared = clear_stale_llave_after_software_swap(gc_root)
                if cleared:
                    note = llave_bind_note_after_clear(cleared)
                    _emit(progress, f"Cleared {len(cleared)} stale LLAVE token(s)", lines)
                    if note:
                        _emit(progress, note, lines)
            else:
                _emit(progress, "SkipClearTrialTokens (revert / preserve bind)", lines)

            if not skip_launch:
                _winrm_run_file(
                    ip=host,
                    remote_path=run_ps1,
                    args=["-AlreadyElevated"],
                    timeout=300,
                    lines=lines,
                    progress=progress,
                    label="Run-FullStack",
                )
                if wait_for_game:
                    _wait_for_ruleta(host, progress=progress, lines=lines)
            else:
                _emit(progress, "SkipLaunch", lines)

        _emit(progress, "DONE OK", lines)
        return SwapResult(True, "\n".join(lines))
    except Exception as e:  # noqa: BLE001
        logger.exception("software version swap failed")
        _emit(progress, f"FAIL: {e}", lines)
        return SwapResult(False, "\n".join(lines))
