"""Write-scope segmentation for Config Scanner EGM push (HW / SW / full)."""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

from config_scanner.xml_diff import FileDiff


class WriteScope(str, Enum):
    """What to push when writing a snapshot (or filtering the compare panel)."""

    FULL = "full"
    HARDWARE = "hardware"
    SOFTWARE = "software"
    NO_PAYTABLE = "no_paytable"
    FULL_SOFTWARE = "full_software"
    BINARIES_ONLY = "binaries_only"


WRITE_SCOPE_LABELS: dict[WriteScope, str] = {
    WriteScope.FULL: "Full config",
    WriteScope.HARDWARE: "Hardware only",
    WriteScope.SOFTWARE: "Software only",
    WriteScope.NO_PAYTABLE: "Config except paytables",
    WriteScope.FULL_SOFTWARE: "Config + Ruleta software",
    WriteScope.BINARIES_ONLY: "Ruleta software only (keep cabinet profile)",
}

WRITE_SCOPE_DESCRIPTIONS: dict[WriteScope, str] = {
    WriteScope.FULL: "All config except serialport/ and EGM identity",
    WriteScope.HARDWARE: "Bill / ticket / switches / LEDs / counters / SAS / CommCtrl",
    WriteScope.SOFTWARE: "Ruleta / mgconfig / themes / app behaviour",
    WriteScope.NO_PAYTABLE: (
        "All restorable config except paytable JSON — use when live Ruleta "
        "cannot load the snapshot paytables"
    ),
    WriteScope.FULL_SOFTWARE: (
        "Restore config, then push the matching Ruleta software pack "
        "(software_versions) so paytables and the exe stay aligned"
    ),
    WriteScope.BINARIES_ONLY: (
        "Push matching Ruleta binaries only. Keep this cabinet's setup, "
        "switches, wheel, SAS, serialport, and licence. Refuses the "
        "10.2.0.684 trial and Downloads 10.2.0.0."
    ),
}

WRITE_SCOPE_SHORT: dict[WriteScope, str] = {
    WriteScope.FULL: "full config",
    WriteScope.HARDWARE: "hardware",
    WriteScope.SOFTWARE: "software",
    WriteScope.NO_PAYTABLE: "config except paytables",
    WriteScope.FULL_SOFTWARE: "config + Ruleta software",
    WriteScope.BINARIES_ONLY: "Ruleta binaries (keep profile)",
}


# Never auto-write board COM maps (see never-edit-serialport-layout rule).
_SERIALPORT_DIR_RE = re.compile(r"(^|/)serialport(/|$)", re.IGNORECASE)

# Segment tokens (no trailing slash) so children like hardware/serialport/… match.
# SASControler1 / SASControler2 use a trailing instance digit on cabinets.
_HARDWARE_PATH_RE = re.compile(
    r"(^|/)"
    r"("
    r"driverssetup|"
    r"hwdrivers|"
    r"hwsubsys|"
    r"modules/hw|"
    r"application/hw|"
    r"commctrl(?:sas)?|"
    r"(?:aurum/)?sascontroler\d*|"
    r"endpoints|"
    r"hardware|"
    r"xyntservice\d*/commctrl[^/]*|"
    r"service\.d/commctrl[^/]*"
    r")"
    r"(/|$)",
    re.IGNORECASE,
)

# Device-specific HW that lives outside …/hardware/ (switches, bill, ticket, LEDs, counters).
# Do not match ruleta UI names like jackpotCountersScreen or Mechanical.xml layouts.
_HARDWARE_DEVICE_RE = re.compile(
    r"("
    r"(^|/)game/switches\.xml$|"
    r"(^|/)game-switches\.xml$|"
    r"(^|/)hwsubsys-[^/]+$|"
    r"goldclub\.hw\.subsys\.driver|"
    r"externalgamebilldeviceinfo|"
    r"externalticketprinterdeviceinfo|"
    r"(^|/)service\.d/hwsubsys[^/]*$"
    r")",
    re.IGNORECASE,
)

# Explicit SW behaviour trees (roulette + slot multigamer).
_SOFTWARE_PATH_RE = re.compile(
    r"(^|/)"
    r"("
    r"application/ruleta|"
    r"themes|"
    r"languages|"
    r"mgconfig\.xml|"
    r"texts\.xml|"
    r"appsettings|"
    r"skin\.xml|"
    r"godot\.xml|"
    r"gameconfig|"
    r"xml-configs/application/ruleta"
    r")"
    r"(/|$)",
    re.IGNORECASE,
)


def normalize_rel_path(relative_path: str) -> str:
    return (relative_path or "").replace("\\", "/").strip("/")


def is_protected_write_path(relative_path: str) -> bool:
    """True for the whole serialport tree and machine-identity files — never bulk-write."""
    from config_scanner.machine_identity import is_protected_machine_identity_path
    from roulette_trial import is_trial_persist_rel_path

    norm = normalize_rel_path(relative_path)
    if not norm:
        return False
    if is_protected_machine_identity_path(norm):
        return True
    if is_trial_persist_rel_path(norm):
        return True
    if _SERIALPORT_DIR_RE.search(norm):
        return True
    return False


def protected_write_block_reason(relative_path: str) -> str | None:
    """Human-readable reason when ``is_protected_write_path`` is True."""
    from config_scanner.machine_identity import (
        is_protected_machine_identity_path,
        protected_machine_identity_reason,
    )

    norm = normalize_rel_path(relative_path)
    if not norm:
        return None
    if is_protected_machine_identity_path(norm):
        return protected_machine_identity_reason(norm)
    from roulette_trial import is_trial_persist_rel_path

    if is_trial_persist_rel_path(norm):
        return (
            "Blocked: Ruleta trial tokens in persistent/ (RouletteActivate.dat, "
            "FinanceStamps, Password.dat) must not be restored from a snapshot. "
            "An expired SUCCEEDED token brings ERROR 30 back. Use Clear ERROR 30 / 99."
        )
    if _SERIALPORT_DIR_RE.search(norm):
        return (
            "Blocked: serialport/ must not be written from Config Scanner "
            "(board COM maps and port wiring)."
        )
    return None


def is_hardware_path(relative_path: str) -> bool:
    """Bill/ticket/switches/LEDs/counters/SAS/CommCtrl-style hardware config."""
    norm = normalize_rel_path(relative_path)
    if not norm or is_protected_write_path(norm):
        return False
    if _HARDWARE_PATH_RE.search(norm):
        return True
    return bool(_HARDWARE_DEVICE_RE.search(norm))


def is_paytable_config_path(relative_path: str) -> bool:
    """True for archived paytable JSON (skipped by the no-paytable scope)."""
    norm = normalize_rel_path(relative_path).casefold()
    if not norm:
        return False
    if "/paytables/" in f"/{norm}/":
        return True
    name = Path(norm).name
    return name.startswith("paytable_") and name.endswith(".json")


def is_software_path(relative_path: str) -> bool:
    """Gameplay / behaviour config (ruleta setup, mgconfig, themes, …)."""
    norm = normalize_rel_path(relative_path)
    if not norm or is_protected_write_path(norm):
        return False
    if is_hardware_path(norm):
        return False
    if _SOFTWARE_PATH_RE.search(norm):
        return True
    # Remaining non-HW application/config files count as software behaviour.
    if "/application/" in f"/{norm.casefold()}/" or norm.casefold().startswith("config/"):
        return True
    if norm.casefold().startswith("data/"):
        return True
    return False


def path_matches_write_scope(relative_path: str, scope: WriteScope | str) -> bool:
    """Return True when ``relative_path`` belongs in the selected write scope."""
    try:
        scope_e = WriteScope(scope) if not isinstance(scope, WriteScope) else scope
    except ValueError:
        scope_e = WriteScope.FULL
    if is_protected_write_path(relative_path):
        return False
    if scope_e is WriteScope.BINARIES_ONLY:
        return False
    if scope_e is WriteScope.FULL or scope_e is WriteScope.FULL_SOFTWARE:
        return True
    if scope_e is WriteScope.NO_PAYTABLE:
        return not is_paytable_config_path(relative_path)
    if scope_e is WriteScope.HARDWARE:
        return is_hardware_path(relative_path)
    return is_software_path(relative_path)


def filter_relative_paths(
    relative_paths: list[str] | tuple[str, ...],
    scope: WriteScope | str,
) -> list[str]:
    return [p for p in relative_paths if path_matches_write_scope(p, scope)]


def filter_file_diffs_for_write_scope(
    file_diffs: list[FileDiff],
    scope: WriteScope | str,
) -> list[FileDiff]:
    """Keep changed-file cards that match the write scope.

    Prefer not to use this for the compare panel (bulk write is scoped
    separately so per-setting Write stays reachable). Kept for callers that
    want a scoped file list.
    """
    try:
        scope_e = WriteScope(scope) if not isinstance(scope, WriteScope) else scope
    except ValueError:
        scope_e = WriteScope.FULL
    if scope_e is WriteScope.BINARIES_ONLY:
        return []
    if scope_e is WriteScope.FULL or scope_e is WriteScope.FULL_SOFTWARE:
        return list(file_diffs)
    return [
        item
        for item in file_diffs
        if path_matches_write_scope(item.relative_path, scope_e)
    ]


def classify_path(relative_path: str) -> str:
    """Return ``protected`` | ``hardware`` | ``software`` | ``other``."""
    if is_protected_write_path(relative_path):
        return "protected"
    if is_hardware_path(relative_path):
        return "hardware"
    if is_software_path(relative_path):
        return "software"
    return "other"
