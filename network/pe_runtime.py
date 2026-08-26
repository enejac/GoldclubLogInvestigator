"""PE sidecar inventory for Ruleta version swaps.

10.2.0.876 failed with a missing LIBEAY32.dll because the surgical pack
only copied seven files. Both 10.1 and 10.2 also name Boost / VLC /
SQLite next to the exe; those usually survive a swap, until they do not.

This module reads DLL names out of Ruleta.exe, keeps only local runtimes
(not KERNEL32 / GoldClub.*.dll), and harvests matching-arch copies.
32-bit ``services\\ntp\\bin\\libeay32.dll`` is never accepted for x64 Ruleta.
Licence.dll is never copied (live WIBU stays).
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterable
from pathlib import Path

_PE_AMD64 = 0x8664
_PE_I386 = 0x14C
_DLL_RE = re.compile(rb"([A-Za-z0-9_\-.]{3,80}\.dll)", re.IGNORECASE)

SYSTEM_DLL_NAMES: frozenset[str] = frozenset(
    {
        "advapi32.dll",
        "atlthunk.dll",
        "comctl32.dll",
        "comdlg32.dll",
        "dbghelp.dll",
        "gdi32.dll",
        "imagehlp.dll",
        "kernel32.dll",
        "msimg32.dll",
        "msvcp140.dll",
        "msvcrt.dll",
        "ntdll.dll",
        "ole32.dll",
        "oleaut32.dll",
        "shell32.dll",
        "shlwapi.dll",
        "ucrtbase.dll",
        "user32.dll",
        "vcruntime140.dll",
        "version.dll",
        "winmm.dll",
        "ws2_32.dll",
    }
)

SKIP_SIDECAR_NAMES: frozenset[str] = frozenset(
    {"licence.dll", "license.dll"}
)

# 10.2.0.876 WebApiProxy references this assembly; the 7-file pack never
# included it, so a 10.1 leftover (2.0.9708.*) stays and TypeLoadException
# follows. Harvest a newer copy from VHD / donor lib/. Never licence.dll.
MANAGED_DECLARATIONS_REL = "lib/GoldClub.ManagedDeclarations.dll"
MANAGED_DECLARATIONS_NAME = "GoldClub.ManagedDeclarations.dll"

LOCAL_RUNTIME_NAMES: frozenset[str] = frozenset(
    {
        "libeay32.dll",
        "ssleay32.dll",
        "libvlc.dll",
        "libvlccore.dll",
        "sqlite3.dll",
    }
)

LOCAL_RUNTIME_PREFIXES: tuple[str, ...] = ("boost_",)

# Loaded by the named DLL even when Ruleta.exe does not spell them.
COMPANION_DLLS: dict[str, tuple[str, ...]] = {
    "libeay32.dll": ("ssleay32.dll",),
    "libvlc.dll": ("libvlccore.dll",),
}


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


def is_system_dll(name: str) -> bool:
    key = Path(name).name.casefold()
    if key in SYSTEM_DLL_NAMES:
        return True
    return key.startswith("api-ms-win-")


def is_local_runtime_dll(name: str) -> bool:
    key = Path(name).name.casefold()
    if key in SKIP_SIDECAR_NAMES or is_system_dll(key):
        return False
    if key in LOCAL_RUNTIME_NAMES:
        return True
    return any(key.startswith(prefix) for prefix in LOCAL_RUNTIME_PREFIXES)


def pe_named_dlls(exe: Path) -> tuple[str, ...]:
    """DLL basenames that appear as ASCII strings in the PE."""
    try:
        data = Path(exe).read_bytes()
    except OSError:
        return ()
    found: list[str] = []
    seen: set[str] = set()
    for match in _DLL_RE.finditer(data):
        name = match.group(1).decode("ascii", "ignore")
        key = name.casefold()
        if key in seen or not key.endswith(".dll"):
            continue
        seen.add(key)
        found.append(name)
    return tuple(found)


def required_local_runtime(exe: Path) -> tuple[str, ...]:
    """Local sidecar basenames the exe names (licence / system DLLs dropped)."""
    need: list[str] = []
    seen: set[str] = set()
    for name in pe_named_dlls(exe):
        if not is_local_runtime_dll(name):
            continue
        key = Path(name).name.casefold()
        if key in seen:
            continue
        seen.add(key)
        need.append(Path(name).name)
    return tuple(need)


def companion_runtime(required: Iterable[str]) -> tuple[str, ...]:
    extra: list[str] = []
    seen: set[str] = {Path(name).name.casefold() for name in required}
    for name in required:
        for companion in COMPANION_DLLS.get(Path(name).name.casefold(), ()):
            if companion.casefold() in seen:
                continue
            seen.add(companion.casefold())
            extra.append(companion)
    return tuple(extra)


def sidecar_search_roots(ruleta_root: Path) -> tuple[Path, ...]:
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
    found: list[Path] = []
    for letter in ("R", "P"):
        for rel in ("ruleta", "goldclub/ruleta", "Goldclub/ruleta"):
            root = Path(f"{letter}:/") / rel
            try:
                if (root / "Ruleta.exe").is_file() or (root / "libeay32.dll").is_file():
                    found.append(root)
            except OSError:
                continue
    return tuple(found)


def find_matching_sidecar(
    name: str,
    roots: Iterable[Path],
    machine: int | None,
) -> Path | None:
    """First copy of ``name`` whose PE machine matches (or is unknown)."""
    want = Path(name).name
    for root in roots:
        candidate = Path(root) / want
        try:
            if not candidate.is_file():
                continue
        except OSError:
            continue
        found = pe_machine(candidate)
        want_machine = machine if machine is not None else _PE_AMD64
        if found is not None and found != want_machine:
            continue
        return candidate
    return None


def missing_local_runtime(
    exe: Path,
    *search_roots: Path,
) -> tuple[str, ...]:
    """Named local sidecars that are absent (or wrong arch) under the roots."""
    if not Path(exe).is_file():
        return ()
    machine = pe_machine(exe) or _PE_AMD64
    roots = tuple(Path(root) for root in search_roots if root is not None)
    missing: list[str] = []
    for name in required_local_runtime(exe):
        if find_matching_sidecar(name, roots, machine) is None:
            missing.append(Path(name).name.casefold())
    return tuple(missing)


def pe_file_version_tuple(path: Path) -> tuple[int, int, int, int] | None:
    """VS_FIXEDFILEINFO FileVersion, or None when the PE has no resource."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    sig = b"\xbd\x04\xef\xfe"
    start = 0
    found: list[tuple[int, int, int, int]] = []
    while True:
        off = data.find(sig, start)
        if off < 0 or off + 16 > len(data):
            break
        start = off + 4
        struct_ver = int.from_bytes(data[off + 4 : off + 8], "little")
        if struct_ver not in {0x00010000, 0x00000000}:
            continue
        ms = int.from_bytes(data[off + 8 : off + 12], "little")
        ls = int.from_bytes(data[off + 12 : off + 16], "little")
        ver = (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
        if ver[0] > 200 or ver == (0, 0, 0, 0):
            continue
        found.append(ver)
    return found[0] if found else None


def harvest_managed_declarations(
    dest_ruleta: Path,
    extra_roots: tuple[Path, ...] = (),
) -> str | None:
    """Replace dest ManagedDeclarations when a newer donor copy exists."""
    dest = Path(dest_ruleta)
    dest_lib = dest / "lib" / MANAGED_DECLARATIONS_NAME
    live_ver = pe_file_version_tuple(dest_lib) if dest_lib.is_file() else None
    roots: list[Path] = [dest, dest / "lib"]
    roots.extend(extra_roots)
    for vhd in mounted_vhd_ruleta_roots():
        roots.append(vhd)
        roots.append(vhd / "lib")
    best: Path | None = None
    best_ver: tuple[int, int, int, int] = live_ver or (0, 0, 0, 0)
    seen: set[str] = set()
    for root in roots:
        candidate = Path(root) / MANAGED_DECLARATIONS_NAME
        try:
            key = str(candidate.resolve()).casefold()
        except OSError:
            key = str(candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            if not candidate.is_file():
                continue
        except OSError:
            continue
        ver = pe_file_version_tuple(candidate)
        if ver is None or ver <= best_ver:
            continue
        best = candidate
        best_ver = ver
    if best is None:
        return None
    try:
        if dest_lib.is_file() and best.resolve() == dest_lib.resolve():
            return None
    except OSError:
        pass
    dest_lib.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, dest_lib)
    live = ".".join(str(n) for n in (live_ver or (0, 0, 0, 0)))
    new = ".".join(str(n) for n in best_ver)
    return f"harvested {MANAGED_DECLARATIONS_NAME} {new} over {live}"


def harvest_local_runtime(
    ruleta_root: Path,
    dest_ruleta: Path,
    extra_roots: tuple[Path, ...] = (),
) -> list[str]:
    """Copy named local sidecars (plus companions) next to dest Ruleta.exe."""
    dest = Path(dest_ruleta)
    dest.mkdir(parents=True, exist_ok=True)
    exe = dest / "Ruleta.exe"
    if not exe.is_file():
        exe = Path(ruleta_root) / "Ruleta.exe"
    if not exe.is_file():
        return []
    machine = pe_machine(exe) or _PE_AMD64
    roots = (
        *sidecar_search_roots(ruleta_root),
        *extra_roots,
        dest,
    )
    if required_local_runtime(exe):
        roots = (*roots, *mounted_vhd_ruleta_roots())
    names = list(required_local_runtime(exe))
    names.extend(companion_runtime(names))
    # Extra Boost / VLC companions that already sit next to the live exe.
    # Do not pull leftover 10.2 OpenSSL off a mounted VHD into a 10.1 pack.
    try:
        for path in Path(ruleta_root).glob("*.dll"):
            if is_local_runtime_dll(path.name):
                names.append(path.name)
    except OSError:
        pass
    copied: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = Path(name).name.casefold()
        if key in seen or key in SKIP_SIDECAR_NAMES:
            continue
        seen.add(key)
        src = find_matching_sidecar(name, roots, machine)
        if src is None:
            continue
        out = dest / src.name
        try:
            if src.resolve() == out.resolve():
                copied.append(out.name)
                continue
        except OSError:
            pass
        shutil.copy2(src, out)
        copied.append(out.name)
    return copied


def runtime_refuse_reason(
    source_ruleta: Path,
    dest_ruleta: Path | None = None,
) -> str | None:
    """Refuse a push whose dest Ruleta.exe would miss a named local sidecar."""
    exe = Path(source_ruleta) / "Ruleta.exe"
    if not exe.is_file():
        return None
    roots = [Path(source_ruleta)]
    if dest_ruleta is not None:
        roots.append(Path(dest_ruleta))
    missing = missing_local_runtime(exe, *roots)
    if not missing:
        return None
    listed = ", ".join(missing)
    return (
        f"Ruleta.exe needs 64-bit {listed} next to the exe. "
        "This pack does not have "
        + ("it" if len(missing) == 1 else "them")
        + ". The NTP copy under services\\ntp\\bin is 32-bit and will not "
        "load (ruleta.exe System Error). Add the x64 DLL from a full drop "
        "or a mounted goldclub.vhd (R:\\ruleta), then restore again."
    )
