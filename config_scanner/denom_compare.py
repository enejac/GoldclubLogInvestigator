"""Denom catalog vs active-rate compare policy.

Factory / default images list every possible credit denomination in
``DenominationList``. The game does not play that menu: it uses
``CreditRateValues`` and ``SingleDenomination`` (lab cabinets usually ``1``).

Comparing those catalogs leaf-by-leaf painted unused entries red. Treat the
menu as informational whenever the live rate is a subset lock of a factory
catalog, and only keep a real (red) fault when the active rate actually
changes to a disjoint set.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config_scanner.xml_diff import ContentChange

CATALOG_PARENT_NAMES: frozenset[str] = frozenset(
    {
        "denominationlist",
        "creditratevalues",
    }
)
ACTIVE_RATE_NAMES: frozenset[str] = frozenset(
    {
        "creditratevalues",
        "singledenomination",
    }
)
# A factory image lists many menu values; a site/debug lock is one or two.
_FACTORY_CATALOG_MIN = 5

_SPLIT_RE = re.compile(r"[,\s]+")


def path_leaf_name(path: str) -> str:
    segment = (path or "").replace("\\", "/").rsplit("/", 1)[-1]
    return segment.split("[", 1)[0]


def is_denom_list_parent_name(name: str) -> bool:
    return (name or "").casefold() in CATALOG_PARENT_NAMES


def is_denom_catalog_path(path: str) -> bool:
    """True for ``DenominationList`` itself or a leftover ``.../int`` under it."""
    norm = f"/{(path or '').replace(chr(92), '/').casefold()}"
    return "/denominationlist" in norm


def is_active_rate_path(path: str) -> bool:
    return path_leaf_name(path).casefold() in ACTIVE_RATE_NAMES


def parse_denom_list(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    text = value.strip()
    if not text:
        return ()
    return tuple(part for part in _SPLIT_RE.split(text) if part)


def looks_like_factory_catalog(values: tuple[str, ...]) -> bool:
    return len(values) >= _FACTORY_CATALOG_MIN


def is_expected_rate_lock(old: tuple[str, ...], new: tuple[str, ...]) -> bool:
    """True when one side is a non-empty subset of a factory-length catalog."""
    if not old or not new:
        return False
    old_set, new_set = set(old), set(new)
    if old_set == new_set:
        return True
    if old_set < new_set and looks_like_factory_catalog(new):
        return True
    if new_set < old_set and looks_like_factory_catalog(old):
        return True
    return False


def is_catalog_content_change(change: ContentChange) -> bool:
    return change.change_type == "catalog"


def classify_denom_changes(changes: list[ContentChange]) -> list[ContentChange]:
    """Downgrade factory-catalog / 1-cent lock diffs to ``catalog`` (not red)."""
    classified: list[ContentChange] = []
    for change in changes:
        if is_denom_catalog_path(change.path):
            classified.append(replace(change, change_type="catalog"))
            continue
        if is_active_rate_path(change.path) and is_expected_rate_lock(
            parse_denom_list(change.old_value),
            parse_denom_list(change.new_value),
        ):
            classified.append(replace(change, change_type="catalog"))
            continue
        classified.append(change)
    return classified
