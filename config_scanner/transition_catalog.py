"""Known Ruleta 10.1 / 10.2 interchange facts (lab: GRT330106 / 10.2.0.876).

Measured from Ruleta.exe strings and live XML:

* 10.2 imports LIBEAY32.dll; 10.1 does not. Both name Boost / VLC / SQLite.
* 10.2 no longer contains enable player select, the dynamic_* paytable
  enums, or show online bonusing popup.
* 10.2 paytables are signed JSON (compatiblewheeltypes, signature) with
  paytable_elite_double_zero / paytable_premium_double_zero.
* 10.2.0.684 is the Development trial (ERROR 30). 10.2.0.0 exits silently.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VersionFacts:
    """What this major.minor understands and refuses."""

    major_minor: str
    obsolete_setting_names: tuple[str, ...]
    added_setting_names: tuple[str, ...]
    added_paytable_ids: tuple[str, ...]
    removed_paytable_ids: tuple[str, ...]


FACTS_10_1 = VersionFacts(
    major_minor="10.1",
    obsolete_setting_names=(),
    added_setting_names=(
        "enable player select",
        "EnablePlayerSelect",
        "show online bonusing popup",
    ),
    added_paytable_ids=(
        "paytable_double_zero",
        "paytable_premium",
        "paytable_elite",
        "paytable_player_select",
        "dynamic_elite",
        "dynamic_playerselect",
        "dynamic_premium",
        "dynamic_standard",
    ),
    removed_paytable_ids=(),
)

FACTS_10_2 = VersionFacts(
    major_minor="10.2",
    obsolete_setting_names=(
        "enable player select",
        "EnablePlayerSelect",
        "show online bonusing popup",
    ),
    added_setting_names=(
        "compatiblelayouts",
        "compatiblewheeltypes",
        "isuserselect",
        "validate wheel compatibility",
    ),
    added_paytable_ids=(
        "paytable_elite_double_zero",
        "paytable_premium_double_zero",
    ),
    removed_paytable_ids=(
        "dynamic_elite",
        "dynamic_playerselect",
        "dynamic_premium",
        "dynamic_standard",
        "paytable_player_select",
    ),
)

FACTS_BY_MAJOR_MINOR: dict[str, VersionFacts] = {
    "10.1": FACTS_10_1,
    "10.2": FACTS_10_2,
}

REFUSED_BUILDS: dict[str, str] = {
    "10.2.0.684": "ERROR 30 Development trial / LLAVE",
    "10.2.0.0": "silent ProcessExit (Downloads 10.2.0.0)",
}


def facts_for(major_minor: str | None) -> VersionFacts | None:
    key = (major_minor or "").strip()
    if not key:
        return None
    if key in FACTS_BY_MAJOR_MINOR:
        return FACTS_BY_MAJOR_MINOR[key]
    short = ".".join(key.split(".")[:2])
    return FACTS_BY_MAJOR_MINOR.get(short)


def refuse_reason_for_build(product_version: str | None) -> str | None:
    text = (product_version or "").strip()
    if not text:
        return None
    for build, reason in REFUSED_BUILDS.items():
        if build in text:
            return f"Refusing Ruleta {build}: {reason}."
    return None
