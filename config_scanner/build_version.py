"""Parse BuildVersion.txt and locate the game image drive."""

from __future__ import annotations

import glob
import hashlib
import json
import re
import socket
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from config_scanner.profiles import GameProfile, display_profile_label


@dataclass(frozen=True)
class BuildInfo:
    source_version: str | None
    branch: str | None
    product_version: str | None
    build_number: str | None
    build_date: str | None
    trigger: str | None
    requested_by: str | None
    scan_timestamp: str
    game_drive: str
    profile_id: str | None = None
    profile_label: str | None = None
    exe_product_version: str | None = None
    exe_file_version: str | None = None
    exe_product_name: str | None = None
    # Cabinet MachineName from ProductSerialNumber.json (e.g. GRT330106 / GST20664)
    machine_serial: str | None = None


def format_build_info_log_line(info: BuildInfo) -> str:
    """One-line version summary for scan logs and reports."""
    software = format_software_display(info)
    parts: list[str] = []
    if info.machine_serial:
        parts.append(f"SN={info.machine_serial}")
    if software:
        parts.append(software)
    if info.build_number and f"build {info.build_number}" not in software.casefold():
        parts.append(f"build={info.build_number}")
    if info.source_version:
        parts.append(f"sourceVersion={info.source_version}")
    if info.exe_product_name or info.exe_product_version:
        exe_label = info.exe_product_name or "exe"
        if info.exe_product_version:
            parts.append(f"{exe_label}={info.exe_product_version}")
        if info.exe_file_version and info.exe_file_version != info.exe_product_version:
            parts.append(f"fileVersion={info.exe_file_version}")
    return ", ".join(parts) if parts else "version unknown"


def software_product_family(profile_id: str | None) -> str:
    """Human product family for snapshot names and reports."""
    pid = (profile_id or "").strip().casefold()
    if pid == "slot_lab_90" or pid.startswith("slot"):
        return "Slot"
    # Roulette USB / Alegro Wing cabinets (Ruleta Module).
    return "Ruleta Alegro Wing"


def _safe_folder_token(value: str | None, *, fallback: str = "unknown") -> str:
    text = (value or "").strip() or fallback
    text = re.sub(r"[^\w.\-]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("._-")
    return text or fallback



_UNC_HOST_RE = re.compile(r"^\\\\([^\\]+)\\", re.IGNORECASE)
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _machine_name_from_product_serial_json(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = str(data.get("MachineName") or "").strip()
    if not raw:
        kind = str(data.get("ProductKind") or "").strip().upper()
        serial = str(data.get("ProductSerialNumber") or "").strip()
        if kind and serial and serial.isdigit():
            raw = f"G{kind}{serial}"
        elif serial:
            raw = serial
    if not raw:
        return None
    token = _safe_folder_token(raw.upper(), fallback="")
    return token or None


def read_machine_serial_from_target(target: str | None) -> str | None:
    """
    Resolve cabinet MachineName / SN for snapshot folder names.

    Prefers ``var/state/maintenance/ProductSerialNumber.json`` under the scan
    image (local or UNC). Falls back to SMB identity helpers when the target
    is a UNC admin share under an IPv4 host.
    """
    norm = normalize_scan_target(target or "")
    if not norm:
        return None

    roots: list[Path] = []
    try:
        root = Path(norm)
        roots.append(root)
        if root.name.casefold() in {"slot", "ruleta", "goldclub"}:
            roots.append(root.parent)
        if root.parent.name.casefold() == "goldclub":
            roots.append(root.parent)
    except OSError:
        pass

    seen: set[str] = set()
    for base in roots:
        for rel in (
            "var/state/maintenance/ProductSerialNumber.json",
            "Goldclub/var/state/maintenance/ProductSerialNumber.json",
        ):
            cand = base / rel
            key = str(cand).casefold()
            if key in seen:
                continue
            seen.add(key)
            name = _machine_name_from_product_serial_json(cand)
            if name:
                return name

    m = _UNC_HOST_RE.match(norm)
    if m:
        host = m.group(1).strip()
        if _IPV4_RE.match(host):
            try:
                from network.fleet_scanner import read_cabinet_machine_name_from_state

                remote = read_cabinet_machine_name_from_state(host)
                if remote:
                    return _safe_folder_token(remote.upper(), fallback="") or None
            except Exception:
                pass
    return None


def attach_machine_serial(info: BuildInfo, target: str | None = None) -> BuildInfo:
    """Fill ``machine_serial`` from the scan target when missing."""
    if (info.machine_serial or "").strip():
        return info
    sn = read_machine_serial_from_target(target or info.game_drive)
    if not sn:
        return info
    return replace(info, machine_serial=sn)



def short_product_version(product_version: str | None) -> str | None:
    """Prefer major.minor (10.2) when a dotted product version is available."""
    raw = (product_version or "").strip()
    if not raw:
        return None
    match = re.match(r"^(\d+)\.(\d+)", raw)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return raw


def preferred_product_version(info: BuildInfo) -> str:
    """PE Details ProductVersion first — BuildVersion.txt / branch can lag a swap."""
    return (
        (info.exe_product_version or "").strip()
        or (info.product_version or "").strip()
    )


def format_software_display(info: BuildInfo) -> str:
    """
    Clear product line for UI/reports, e.g.
    ``Ruleta Alegro Wing software version 10.2 (10.2.0.684, build 40097)``.
    """
    family = software_product_family(info.profile_id)
    full = preferred_product_version(info)
    short = short_product_version(full)
    build = (info.build_number or "").strip()
    if short and full and short != full:
        ver = f"version {short} ({full}"
        if build:
            ver += f", build {build})"
        else:
            ver += ")"
    elif short or full:
        ver = f"version {short or full}"
        if build:
            ver += f" (build {build})"
    elif build:
        ver = f"build {build}"
    else:
        ver = "version unknown"
    return f"{family} software {ver}"


def snapshot_software_token(info: BuildInfo) -> str:
    """Filesystem-safe software token used inside snapshot folder names.

    Uses the full PE Details ProductVersion (``10.2.0.876``), not the
    shortened ``10.2`` from BuildVersion.txt.
    """
    family = software_product_family(info.profile_id)
    family_token = _safe_folder_token(family.replace(" ", "_"))
    full = preferred_product_version(info)
    build = (info.build_number or "unknown").strip()
    if info.profile_id == "slot_lab_90":
        return f"Slot_b{_safe_folder_token(build)}"
    ver_token = _safe_folder_token(full or "unknown")
    if ver_token and not ver_token.lower().startswith("v"):
        ver_token = f"v{ver_token}"
    return f"{family_token}_{ver_token}_b{_safe_folder_token(build)}"



def is_unc_path(target: str) -> bool:
    return target.strip().startswith("\\\\")


def _local_host_keys() -> set[str]:
    keys: set[str] = {"localhost", "127.0.0.1", "::1"}
    try:
        keys.add(socket.gethostname().casefold())
        keys.add(socket.getfqdn().casefold())
        for info in socket.getaddrinfo(socket.gethostname(), None):
            keys.add(str(info[4][0]).casefold())
    except OSError:
        pass
    return keys


def is_local_host(host: str) -> bool:
    return host.strip().casefold() in _local_host_keys()


def unc_admin_share_to_local_path(target: str) -> str | None:
    """Map \\\\host\\c$\\Goldclub\\slot to C:\\Goldclub\\slot when running on that host."""
    norm = normalize_scan_target(target)
    if not is_unc_path(norm):
        return None
    match = re.match(r"^\\\\([^\\]+)\\c\$\\(.+)$", norm, re.IGNORECASE)
    if not match:
        return None
    host, tail = match.group(1), match.group(2)
    if not is_local_host(host):
        return None
    local = Path("C:/") / tail.replace("\\", "/")
    if local.exists():
        return normalize_scan_target(str(local))
    return None


def prefer_local_scan_target(target: str) -> str:
    """Use a local path instead of a loopback admin share when equivalent."""
    local = unc_admin_share_to_local_path(target)
    return local or normalize_scan_target(target)


# Removable / game-image drives probed before the full alphabet sweep (G: is common on cabinets).
_PRIORITY_GAME_DRIVES = ("G:", "D:", "E:", "F:", "H:", "C:")

# Lab cabinets probed over UNC when no local game image is mounted.
_LAB_REMOTE_GAME_ROOTS = (
    r"\\10.0.0.90\c$\Goldclub",
    r"\\10.0.0.90\c$\Goldclub\slot",
    r"\\10.0.0.90\d$\Goldclub",
    r"\\10.0.0.90\d$\Goldclub\slot",
    r"\\10.0.0.111\slot",
    r"\\10.0.0.111\c$\Goldclub",
)

# Maintenance RAM-clear scripts — secondary roulette USB marker when BuildVersion.txt is absent.
_RAMCLEAR_SCRIPT_REL_PATHS = (
    "maintenance/tasks/ramclear.ps1",
    "maintenance/tasks/ramclear/00-InvokeTask-RamClear.ps1",
    "maintenance/tasks/factory-reset/00-InvokeTask-RamClear.ps1",
    "platform/user/init/onlogon/CheckForRamClear.ps1",
)
_RAMCLEAR_REPO_PREFIXES = ("", "Goldclub/")



def has_slot_game_exe(root: Path) -> bool:
    """True when a real slot client binary exists (folder alone is not enough)."""
    base = Path(root)
    for rel in ("OneHand.exe", "bin/OneHand.exe", "game-start.exe"):
        try:
            if (base / rel).is_file():
                return True
        except OSError:
            continue
    return False


def has_roulette_game_exe(root: Path) -> bool:
    """True when Ruleta.exe exists under the image (folder / scripts alone are not enough)."""
    base = Path(root)
    for rel in ("ruleta/Ruleta.exe", "ruleta/ruleta.exe", "Ruleta.exe", "ruleta.exe"):
        try:
            if (base / rel).is_file():
                return True
        except OSError:
            continue
    return False

def has_ramclear_script(root: Path) -> bool:
    """True when a known RAM-clear maintenance script exists under a game image root."""
    base = Path(root)
    if not base.exists():
        return False
    for prefix in _RAMCLEAR_REPO_PREFIXES:
        for rel in _RAMCLEAR_SCRIPT_REL_PATHS:
            if (base / prefix / rel.replace("\\", "/")).is_file():
                return True
    return False


def roulette_build_version_file(root: Path, relative_path: str) -> Path | None:
    rel = relative_path.replace("\\", "/")
    path = root / rel
    return path if path.is_file() else None


def is_roulette_scan_target(root: Path, *, build_version_relative_path: str) -> bool:
    """Roulette USB: requires Ruleta.exe plus BuildVersion.txt and/or a RAM-clear script.

    An empty Goldclub tree or maintenance scripts alone must not count as installed SW.
    """
    if not has_roulette_game_exe(root):
        return False
    if roulette_build_version_file(root, build_version_relative_path):
        return True
    return has_ramclear_script(root)


def normalize_game_drive(game_drive: str) -> str:
    cleaned = game_drive.strip().rstrip("\\.")
    if is_unc_path(cleaned):
        return cleaned.rstrip("\\")
    if re.fullmatch(r"[A-Za-z]:", cleaned):
        return f"{cleaned[0].upper()}:\\"
    if cleaned.endswith("\\"):
        return cleaned
    return cleaned + "\\"


def normalize_scan_target(target: str) -> str:
    cleaned = target.strip()
    if is_unc_path(cleaned):
        return cleaned.rstrip("\\")
    return normalize_game_drive(cleaned)


def scan_target_path(target: str) -> Path:
    normalized = normalize_scan_target(target)
    if is_unc_path(normalized):
        return Path(normalized)
    if not normalized.endswith("\\"):
        normalized += "\\"
    return Path(normalized)


@dataclass(frozen=True)
class _ExeVersionInfo:
    product_version: str | None
    file_version: str | None
    product_name: str | None
    display_version: str | None


def _find_onehand_exe(scan_root: Path) -> Path | None:
    for rel in ("OneHand.exe", "bin/OneHand.exe"):
        path = scan_root / rel
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _sniff_exe_version_strings(exe_path: Path) -> tuple[str, str | None]:
    try:
        with exe_path.open("rb") as handle:
            blob = handle.read(32 * 1024 * 1024)
    except OSError:
        return "", None
    if not blob:
        return "", None
    text = blob.decode("utf-16le", errors="ignore")
    match = re.search(
        r"(\d+\.\d+\.\d+(?:\.\d+)?[+-]RC\d+(?:[+-][a-zA-Z0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    raw_pv = match.group(1) if match else ""
    if not raw_pv:
        match2 = re.search(r"(\d+\.\d+\.\d+[+-]RC\d+)\b", text, re.IGNORECASE)
        raw_pv = match2.group(1) if match2 else ""
    if not raw_pv:
        match3 = re.search(r"\b(\d+\.\d+\.\d+\.\d+)\b", text)
        raw_pv = match3.group(1) if match3 else ""
    product: str | None = None
    if "GameStar+36" in text:
        product = "GameStar+36"
    elif "JinLong" in text:
        product = "JinLong"
    elif "Ruleta Module" in text:
        product = "Ruleta Module"
    return raw_pv, product


def _normalize_core_version(product_version: str) -> str | None:
    pv = product_version.strip()
    if not pv:
        return None
    base_match = re.search(r"(\d+\.\d+\.\d+)", pv)
    rc_match = re.search(r"(?:\+|-)\s*rc\s*([0-9]{1,3})\b", pv, flags=re.IGNORECASE)
    if not rc_match:
        rc_match = re.search(r"\brc\s*([0-9]{1,3})\b", pv, flags=re.IGNORECASE)
    if base_match:
        base = base_match.group(1)
        rc = f"-rc{rc_match.group(1)}" if rc_match else ""
        return f"v{base}{rc}"
    return None


def _find_ruleta_exe(scan_root: Path) -> Path | None:
    ruleta_dir = scan_root / "ruleta"
    try:
        if not ruleta_dir.is_dir():
            return None
    except OSError:
        return None
    for candidate in (ruleta_dir / "Ruleta.exe", ruleta_dir / "ruleta.exe"):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    try:
        for path in ruleta_dir.glob("*.exe"):
            if path.name.lower() == "ruleta.exe":
                return path
    except OSError:
        pass
    return None


def _read_pe_file_version(win32api: object, exe_str: str) -> str | None:
    try:
        info = win32api.GetFileVersionInfo(exe_str, "\\")
        ms = info["FileVersionMS"]
        ls = info["FileVersionLS"]
        a = (ms >> 16) & 0xFFFF
        b = ms & 0xFFFF
        c = (ls >> 16) & 0xFFFF
        d = ls & 0xFFFF
        if d:
            return f"{a}.{b}.{c}.{d}"
        return f"{a}.{b}.{c}"
    except Exception:
        return None


def _extract_version_from_exe(exe_path: Path, *, slot_style: bool = False) -> _ExeVersionInfo:
    """Read ProductVersion / FileVersion from a game executable."""
    exe_str = str(exe_path)
    product_version = ""
    product_name_meta = ""
    win32api = None
    try:
        import win32api as _win32api  # type: ignore

        win32api = _win32api
    except Exception:
        pass
    try:
        lang_cp: tuple[int, int] | None = None
        file_version: str | None = None
        if win32api is not None:
            file_version = _read_pe_file_version(win32api, exe_str)
            try:
                lang_cp = win32api.GetFileVersionInfo(exe_str, r"\VarFileInfo\Translation")[0]
                lang, codepage = lang_cp
                path = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\ProductVersion"
                product_version = win32api.GetFileVersionInfo(exe_str, path) or ""
            except Exception:
                for fallback in (
                    r"\StringFileInfo\040904b0\ProductVersion",
                    r"\StringFileInfo\000004b0\ProductVersion",
                ):
                    try:
                        product_version = win32api.GetFileVersionInfo(exe_str, fallback) or ""
                        if product_version:
                            break
                    except Exception:
                        continue
            try:
                if lang_cp is not None:
                    lang, codepage = lang_cp
                    pn_path = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\ProductName"
                    product_name_meta = win32api.GetFileVersionInfo(exe_str, pn_path) or ""
                else:
                    raise OSError("no translation")
            except Exception:
                for fallback in (
                    r"\StringFileInfo\040904b0\ProductName",
                    r"\StringFileInfo\000004b0\ProductName",
                ):
                    try:
                        product_name_meta = win32api.GetFileVersionInfo(exe_str, fallback) or ""
                        if product_name_meta:
                            break
                    except Exception:
                        continue

        pv = str(product_version).strip()
        product_name = str(product_name_meta).strip() or None
        if not pv:
            sniff_pv, sniff_pn = _sniff_exe_version_strings(exe_path)
            if sniff_pv:
                pv = sniff_pv.strip()
            if sniff_pn and not product_name:
                product_name = sniff_pn
        if not file_version and win32api is not None:
            file_version = _read_pe_file_version(win32api, exe_str)

        display: str | None
        if slot_style:
            display = _normalize_core_version(pv) if pv else None
            if not display and file_version:
                display = f"v{file_version.rsplit('.', 1)[0]}" if file_version.count(".") >= 2 else f"v{file_version}"
        else:
            display = pv or file_version

        return _ExeVersionInfo(
            product_version=pv or None,
            file_version=file_version,
            product_name=product_name,
            display_version=display,
        )
    except Exception:
        return _ExeVersionInfo(None, None, None, None)


def _extract_version_from_onehand_exe(exe_path: Path) -> _ExeVersionInfo:
    return _extract_version_from_exe(exe_path, slot_style=True)


def _extract_version_from_slotlog(log_dir: Path) -> str | None:
    patterns = [
        str(log_dir / "**" / "*SlotLog*.log"),
        str(log_dir / "SlotLog" / "**" / "*.log"),
        str(log_dir / "**" / "OneHand*" / "**" / "*.log"),
    ]
    files: list[str] = []
    for pattern in patterns:
        try:
            files.extend(glob.glob(pattern, recursive=True))
        except OSError:
            continue
    if not files:
        return None
    files.sort(key=lambda p: Path(p).stat().st_mtime if Path(p).exists() else 0.0, reverse=True)
    version_re = re.compile(
        r"OneHand\.MainFrm\s*-\s*SlotMachine\s+(v[\d.]+(?:-rc\d+)?)",
        re.IGNORECASE,
    )
    for file_path in files[:8]:
        try:
            text = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in reversed(text.splitlines()[-500:]):
            match = version_re.search(line)
            if match:
                return match.group(1)
    return None


def detect_slot_version(scan_root: Path) -> _ExeVersionInfo:
    exe_path = _find_onehand_exe(scan_root)
    if exe_path is not None:
        info = _extract_version_from_onehand_exe(exe_path)
        if info.display_version or info.product_version:
            return info
    log_dir = scan_root.parent / "var" / "log"
    try:
        if log_dir.is_dir():
            core = _extract_version_from_slotlog(log_dir)
            if core:
                return _ExeVersionInfo(
                    product_version=None,
                    file_version=None,
                    product_name=None,
                    display_version=core,
                )
    except OSError:
        pass
    return _ExeVersionInfo(None, None, None, None)


def detect_roulette_exe_version(scan_root: Path) -> _ExeVersionInfo:
    exe_path = _find_ruleta_exe(scan_root)
    if exe_path is None:
        return _ExeVersionInfo(None, None, None, None)
    return _extract_version_from_exe(exe_path, slot_style=False)


def parse_build_version_file(
    build_version_path: Path,
    *,
    game_drive: str,
    scan_timestamp: datetime | None = None,
    profile: GameProfile | None = None,
) -> BuildInfo:
    if not build_version_path.is_file():
        raise FileNotFoundError(f"BuildVersion.txt not found: {build_version_path}")

    values: dict[str, str] = {}
    text = build_version_path.read_text(encoding="utf-8-sig", errors="replace")
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key:
            values[key] = value

    branch = values.get("Branch")
    product_version = None
    if branch:
        match = re.search(r"(\d+\.\d+\.\d+\.\d+)\s*$", branch)
        if match:
            product_version = match.group(1)

    scan_root = build_version_path.parent.parent
    exe_info = detect_roulette_exe_version(scan_root)
    if exe_info.display_version:
        product_version = exe_info.display_version
    elif exe_info.product_version and not product_version:
        product_version = exe_info.product_version

    ts = scan_timestamp or datetime.now().astimezone()
    return BuildInfo(
        source_version=values.get("Source Version"),
        branch=branch,
        product_version=product_version,
        build_number=values.get("Build Number"),
        build_date=values.get("Date"),
        trigger=values.get("Trigger"),
        requested_by=values.get("Requested By"),
        scan_timestamp=ts.isoformat(),
        game_drive=normalize_scan_target(game_drive),
        profile_id=profile.id if profile else None,
        profile_label=(display_profile_label(profile.label, game_drive) if profile else None),
        exe_product_version=exe_info.product_version or exe_info.display_version,
        exe_file_version=exe_info.file_version,
        exe_product_name=exe_info.product_name,
    )


def _build_info_from_binary_fingerprint(
    scan_root: Path,
    profile: GameProfile,
    *,
    scan_timestamp: datetime,
    game_drive: str,
) -> BuildInfo:
    fingerprint = profile.build_fingerprint or {}
    names = list(fingerprint.get("files") or [])
    digest = hashlib.sha1()
    found = 0
    for name in names:
        path = scan_root / str(name)
        if not path.is_file():
            continue
        found += 1
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as handle:
            digest.update(handle.read(65536))
    if found == 0:
        raise FileNotFoundError(
            f"No fingerprint files found under {scan_root} (expected: {', '.join(map(str, names))})"
        )
    if not has_slot_game_exe(scan_root):
        raise FileNotFoundError(
            f"No slot game EXE under {scan_root} "
            "(expected OneHand.exe, bin\\OneHand.exe, or game-start.exe). "
            "An empty C:\\Goldclub\\slot folder is not a valid scan target."
        )
    build_number = digest.hexdigest()[:8].upper()
    version_info = detect_slot_version(scan_root)
    source_version = version_info.product_version or version_info.display_version
    product_version = version_info.display_version
    branch = version_info.product_name or profile.label
    return BuildInfo(
        source_version=source_version,
        branch=branch,
        product_version=product_version,
        build_number=build_number,
        build_date=None,
        trigger="binary_sha1_prefix",
        requested_by=None,
        scan_timestamp=scan_timestamp.isoformat(),
        game_drive=normalize_scan_target(game_drive),
        profile_id=profile.id,
        profile_label=display_profile_label(profile.label, game_drive),
        exe_product_version=version_info.product_version or version_info.display_version,
        exe_file_version=version_info.file_version,
        exe_product_name=version_info.product_name,
    )


def resolve_build_info(
    profile: GameProfile,
    scan_target: str,
    *,
    scan_timestamp: datetime | None = None,
) -> BuildInfo:
    ts = scan_timestamp or datetime.now().astimezone()
    root = scan_target_path(scan_target)
    if profile.build_version_relative_path:
        if is_slot_cabinet_scan_target(scan_target):
            raise FileNotFoundError(
                "BuildVersion.txt is for roulette USB images only, not slot cabinets.\n"
                f"Scan target: {normalize_scan_target(scan_target)}\n\n"
                "Use a slot path with OneHand.exe / game-start.exe / GoldClub.Settings.dll."
            )
        rel = profile.build_version_relative_path.replace("\\", "/")
        build_path = root / rel
        if build_path.is_file():
            if not has_roulette_game_exe(root):
                raise FileNotFoundError(
                    f"Found {rel} but no Ruleta.exe under {root}. "
                    "Empty Goldclub / incomplete image is not a valid roulette scan target."
                )
            return attach_machine_serial(
                parse_build_version_file(
                    build_path,
                    game_drive=scan_target,
                    scan_timestamp=ts,
                    profile=profile,
                ),
                scan_target,
            )
        if has_roulette_game_exe(root) and has_ramclear_script(root):
            return attach_machine_serial(
                BuildInfo(
                    source_version=None,
                    branch="RAM-clear maintenance image",
                    product_version=None,
                    build_number="ramclear",
                    build_date=None,
                    trigger="ramclear_script",
                    requested_by=None,
                    scan_timestamp=ts.isoformat(),
                    game_drive=normalize_scan_target(scan_target),
                    profile_id=profile.id,
                    profile_label=display_profile_label(profile.label, scan_target),
                ),
                scan_target,
            )
        raise FileNotFoundError(
            f"Roulette build tag not found at {build_path}\n"
            f"Scan target: {normalize_scan_target(scan_target)}\n\n"
            "Expected ruleta\\BuildVersion.txt or a RAM-clear maintenance script "
            "(maintenance\\tasks\\ramclear.ps1, CheckForRamClear.ps1, …)."
        )
    if profile.build_fingerprint:
        fp_type = str(profile.build_fingerprint.get("type") or "")
        if fp_type == "binary_sha1_prefix":
            return attach_machine_serial(
                _build_info_from_binary_fingerprint(
                    root,
                    profile,
                    scan_timestamp=ts,
                    game_drive=scan_target,
                ),
                scan_target,
            )
    raise FileNotFoundError(
        f"Profile '{profile.id}' has no build version path or supported fingerprint."
    )


def find_game_drive(
    preferred: str | None = None,
    *,
    build_version_relative_path: str = "ruleta/BuildVersion.txt",
) -> tuple[str, Path]:
    candidates: list[str] = []
    if preferred:
        candidates.append(normalize_game_drive(preferred))
    for letter in range(ord("C"), ord("Z") + 1):
        drive = f"{chr(letter)}:\\"
        if drive not in candidates:
            candidates.append(drive)

    checked: list[str] = []
    rel = build_version_relative_path.replace("\\", "/")
    for drive in candidates:
        root = Path(drive)
        if not root.exists():
            continue
        checked.append(drive)
        build_path = root / rel
        if build_path.is_file():
            return drive, build_path

    raise FileNotFoundError(
        f"Game drive not found. Looked for '{rel}' on: {', '.join(checked) or '(none)'}"
    )


def profile_scan_candidates(profile: GameProfile, preferred: str | None = None) -> list[str]:
    """Ordered scan-target paths to probe for this profile."""
    candidates: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        if not raw or not str(raw).strip():
            return
        norm = normalize_scan_target(str(raw).strip())
        key = norm.casefold()
        if key in seen:
            return
        seen.add(key)
        candidates.append(norm)

    add(preferred)
    add(profile.default_target)
    for raw in profile.discover_targets:
        add(raw)

    if profile.build_version_relative_path:
        for letter in range(ord("C"), ord("Z") + 1):
            add(f"{chr(letter)}:")

    return candidates


def scan_target_is_valid(profile: GameProfile, target: str) -> bool:
    try:
        root = scan_target_path(target)
        if not root.exists():
            return False
        if profile.build_version_relative_path:
            if not has_roulette_game_exe(root):
                return False
        elif profile.build_fingerprint:
            if not has_slot_game_exe(root):
                return False
        resolve_build_info(profile, target)
        return True
    except (FileNotFoundError, OSError, ValueError):
        return False


def discover_scan_target(profile: GameProfile, preferred: str | None = None) -> str:
    """Find a reachable game repo root for the active profile."""
    checked: list[str] = []
    for candidate in profile_scan_candidates(profile, preferred):
        checked.append(candidate)
        if scan_target_is_valid(profile, candidate):
            return normalize_scan_target(candidate)

    if profile.build_version_relative_path:
        marker = profile.build_version_relative_path
    elif profile.build_fingerprint:
        files = profile.build_fingerprint.get("files") or []
        marker = f"fingerprint files ({', '.join(map(str, files))})"
    else:
        marker = "profile markers"
    detail = "\n".join(f"  - {item}" for item in checked) or "  (none)"
    raise FileNotFoundError(
        f"Game repo not found for {profile.label}.\n"
        f"Expected {marker} under one of:\n{detail}"
    )


@dataclass(frozen=True)
class DiscoverResult:
    profile_id: str
    profile_label: str
    target: str


def unified_scan_candidates(
    profiles: list[GameProfile],
    preferred: str | None = None,
) -> list[str]:
    """All paths to probe when auto-detecting slot vs roulette (local before UNC)."""
    candidates: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        if not raw or not str(raw).strip():
            return
        norm = normalize_scan_target(str(raw).strip())
        local_equiv = unc_admin_share_to_local_path(norm)
        if local_equiv:
            norm = local_equiv
        key = norm.casefold()
        if key in seen:
            return
        seen.add(key)
        candidates.append(norm)

    if preferred and not is_unc_path(preferred):
        add(preferred)

    # Local installs / USB images first.
    add(r"C:\Goldclub\slot")
    add(r"C:\Goldclub")
    for drive in _PRIORITY_GAME_DRIVES:
        add(drive)
        add(f"{drive}\\Goldclub")
        add(f"{drive}\\Goldclub\\slot")

    if preferred and is_unc_path(preferred):
        add(preferred)

    # Profile + lab cabinet remotes next (before full alphabet sweep).
    for profile in profiles:
        for raw in profile.discover_targets:
            add(raw)
        add(profile.default_target)
    for remote in _LAB_REMOTE_GAME_ROOTS:
        add(remote)

    # Remaining drive letters last (slow / often empty).
    for letter in range(ord("C"), ord("Z") + 1):
        drive = f"{chr(letter)}:"
        if drive in _PRIORITY_GAME_DRIVES:
            continue
        add(drive)
        add(f"{drive}\\Goldclub")
        add(f"{drive}\\Goldclub\\slot")

    return candidates



def is_slot_cabinet_scan_target(target: str) -> bool:
    """True when the scan path is a slot cabinet tree (not a roulette USB image).

    Roulette lab cabinets often share ``C:\\goldclub`` as ``\\\\ip\\slot``.
    That UNC name ends with ``\\slot`` but the tree is Ruleta, not OneHand.
    """
    norm = normalize_scan_target(target).casefold().rstrip("\\")
    if not (norm.endswith(r"\goldclub\slot") or norm.endswith(r"\slot")):
        return False
    root = scan_target_path(target)
    try:
        exists = root.exists()
    except OSError:
        exists = False
    if exists and has_roulette_game_exe(root) and not has_slot_game_exe(root):
        return False
    return True


def _fingerprint_profiles(profiles: list[GameProfile]) -> list[GameProfile]:
    return [profile for profile in profiles if profile.build_fingerprint]


def _roulette_profiles(profiles: list[GameProfile]) -> list[GameProfile]:
    return [profile for profile in profiles if profile.build_version_relative_path]


def match_profile_for_target(
    target: str,
    profiles: list[GameProfile],
) -> GameProfile | None:
    """Pick slot or roulette profile from repo markers at target."""
    root = scan_target_path(target)
    if not root.exists():
        return None

    if is_slot_cabinet_scan_target(target):
        # Slot cabinets never use ruleta/BuildVersion.txt (roulette USB only).
        for profile in _fingerprint_profiles(profiles):
            if scan_target_is_valid(profile, target):
                return profile
        return None

    for profile in _roulette_profiles(profiles):
        rel = profile.build_version_relative_path or ""
        if is_roulette_scan_target(root, build_version_relative_path=rel) and scan_target_is_valid(
            profile, target
        ):
            return profile

    for profile in _fingerprint_profiles(profiles):
        if scan_target_is_valid(profile, target):
            return profile

    return None


def scan_target_resolution_candidates(
    scan_target: str,
    profiles: list[GameProfile] | None = None,
) -> list[str]:
    """Paths to try when resolving an explicit scan target (USB ConfigScanner subfolder, parents)."""
    explicit = normalize_scan_target(scan_target.strip())
    if not explicit:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        if not raw or not str(raw).strip():
            return
        norm = normalize_scan_target(str(raw).strip())
        key = norm.casefold()
        if key in seen:
            return
        seen.add(key)
        candidates.append(norm)

    add(explicit)
    local_equiv = prefer_local_scan_target(explicit)
    if local_equiv.casefold() != explicit.casefold():
        add(local_equiv)

    path = scan_target_path(explicit)
    if any(part.casefold() == "configscanner" for part in path.parts):
        add(str(path.anchor))
    current = path
    for _ in range(5):
        parent = current.parent
        if parent == current:
            break
        add(str(parent))
        current = parent

    if profiles:
        for candidate in list(candidates):
            profile = match_profile_for_target(candidate, profiles)
            if profile:
                return [prefer_local_scan_target(candidate)]
    return candidates


def resolve_scan_for_target(
    scan_target: str,
    profiles: list[GameProfile],
) -> DiscoverResult:
    """Match slot or roulette at an explicit path only (no drive sweep)."""
    explicit = normalize_scan_target(scan_target.strip())
    if not explicit:
        raise ValueError("scan_target is required")

    # Slot cabinet paths must not walk up into a parent roulette tree
    # (e.g. empty C:\Goldclub\slot must not resolve as C:\Goldclub Ruleta).
    if is_slot_cabinet_scan_target(explicit):
        checked: list[str] = []
        for candidate in (explicit, prefer_local_scan_target(explicit)):
            norm = normalize_scan_target(candidate)
            if not norm or norm in checked:
                continue
            checked.append(norm)
            profile = match_profile_for_target(norm, profiles)
            if profile:
                return DiscoverResult(
                    profile_id=profile.id,
                    profile_label=display_profile_label(profile.label, norm),
                    target=prefer_local_scan_target(norm),
                )
        raise FileNotFoundError(
            f"No slot repo at:\n  {scan_target.strip()}\n\n"
            "Expected slot binaries (OneHand.exe, game-start.exe, GoldClub.Settings.dll). "
            "BuildVersion.txt is roulette-only and is not used under slot folders."
        )

    checked = []
    for candidate in scan_target_resolution_candidates(explicit, profiles=None):
        checked.append(candidate)
        profile = match_profile_for_target(candidate, profiles)
        if profile:
            return DiscoverResult(
                profile_id=profile.id,
                profile_label=display_profile_label(profile.label, candidate),
                target=prefer_local_scan_target(candidate),
            )

    detail = "\n".join(f"  - {item}" for item in checked) or f"  - {explicit}"
    raise FileNotFoundError(
        f"No slot or roulette repo at:\n  {scan_target.strip()}\n\n"
        "Expected Ruleta.exe (+ BuildVersion.txt or RAM-clear script), or slot binaries "
        "(OneHand.exe, game-start.exe, GoldClub.Settings.dll).\n"
        f"Tried:\n{detail}"
    )


def discover_game_repo(
    profiles: list[GameProfile],
    preferred: str | None = None,
) -> DiscoverResult:
    """Auto-detect slot or roulette repo and return matching profile + path."""
    # Local D: roulette with RAM-clear maintenance: prefer that when present.
    if not preferred or normalize_scan_target(preferred).casefold().rstrip("\\") == "d:":
        d_root = scan_target_path("D:")
        if d_root.exists() and has_roulette_game_exe(d_root) and has_ramclear_script(d_root):
            profile = match_profile_for_target("D:", profiles)
            if profile and profile.build_version_relative_path:
                return DiscoverResult(
                    profile_id=profile.id,
                    profile_label=display_profile_label(profile.label, "D:"),
                    target=prefer_local_scan_target("D:"),
                )

    checked: list[str] = []
    for candidate in unified_scan_candidates(profiles, preferred):
        checked.append(candidate)
        profile = match_profile_for_target(candidate, profiles)
        if profile:
            target = prefer_local_scan_target(candidate)
            return DiscoverResult(
                profile_id=profile.id,
                profile_label=display_profile_label(profile.label, target),
                target=target,
            )

    detail = "\n".join(f"  - {item}" for item in checked) or "  (none)"
    raise FileNotFoundError(
        "Game repo not found (slot or roulette).\n"
        "Expected Ruleta.exe (+ BuildVersion.txt or RAM-clear script), or slot binaries "
        "(OneHand.exe, game-start.exe, GoldClub.Settings.dll) under:\n"
        f"{detail}"
    )


def snapshot_folder_name(
    build_number: str | None,
    scan_timestamp: datetime,
    *,
    profile_id: str | None = None,
    product_version: str | None = None,
    exe_product_version: str | None = None,
    machine_serial: str | None = None,
) -> str:
    """
    Snapshot folder name with machine SN + readable software identity.

    Example::
        2026-07-27_GRT330106_Ruleta_Alegro_Wing_v10.2.0.876_b40119_091759
    """
    info = BuildInfo(
        source_version=None,
        branch=None,
        product_version=product_version,
        build_number=build_number,
        build_date=None,
        trigger=None,
        requested_by=None,
        scan_timestamp=scan_timestamp.isoformat(timespec="seconds"),
        game_drive="",
        profile_id=profile_id,
        exe_product_version=exe_product_version,
        machine_serial=machine_serial,
    )
    soft = snapshot_software_token(info)
    stamp = scan_timestamp.strftime("%H%M%S")
    date = scan_timestamp.strftime("%Y-%m-%d")
    sn = _safe_folder_token(machine_serial, fallback="") if machine_serial else ""
    if sn:
        return f"{date}_{sn}_{soft}_{stamp}"
    return f"{date}_{soft}_{stamp}"


def baseline_folder_name(build_info: BuildInfo, *, profile_id: str | None = None) -> str:
    """Friendly snapshot folder name when marking a scan as baseline."""
    resolved = BuildInfo(
        source_version=build_info.source_version,
        branch=build_info.branch,
        product_version=build_info.product_version,
        build_number=build_info.build_number,
        build_date=build_info.build_date,
        trigger=build_info.trigger,
        requested_by=build_info.requested_by,
        scan_timestamp=build_info.scan_timestamp,
        game_drive=build_info.game_drive,
        profile_id=build_info.profile_id or profile_id or "",
        profile_label=build_info.profile_label,
        exe_product_version=build_info.exe_product_version,
        exe_file_version=build_info.exe_file_version,
        exe_product_name=build_info.exe_product_name,
        machine_serial=build_info.machine_serial,
    )
    sn = _safe_folder_token(resolved.machine_serial, fallback="") if resolved.machine_serial else ""
    if (resolved.profile_id or "") == "slot_lab_90":
        return f"{sn}_slot_baseline" if sn else "slot_baseline"
    soft = snapshot_software_token(resolved)
    return f"{sn}_{soft}_baseline" if sn else f"{soft}_baseline"


def is_baseline_folder_name(name: str) -> bool:
    return (
        name == "slot_baseline"
        or name.endswith("_slot_baseline")
        or name.startswith("roulette_baseline_")
        or name.endswith("_baseline")
    )

