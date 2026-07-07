"""
Branded cabinet build label: ``{product_name}_{core_version}`` (e.g. ``JinLong_v2.0.20.0``).
"""

from __future__ import annotations

import re

DEFAULT_PRODUCT_NAME = "GameStar+36"
PRODUCT_VERSION_PLACEHOLDER = "[Product]_[Version]"

_VERSIONISH = re.compile(r"^v?\d+\.\d+", re.IGNORECASE)


def format_product_build_version(product_name: str, core_version: str | None) -> str:
    """
    Return ``{product_name}_{core_version}`` with a normalized ``v…`` core when needed.

    Unknown or empty ``core_version`` yields :data:`PRODUCT_VERSION_PLACEHOLDER`.
    """
    pn = (product_name or DEFAULT_PRODUCT_NAME).strip() or DEFAULT_PRODUCT_NAME
    if core_version is None:
        return PRODUCT_VERSION_PLACEHOLDER
    c = str(core_version).strip()
    if not c or c == "[Version]":
        return PRODUCT_VERSION_PLACEHOLDER
    prefix = f"{pn}_"
    if c.lower().startswith(prefix.lower()):
        c = c[len(prefix) :].lstrip()
        if not c:
            return PRODUCT_VERSION_PLACEHOLDER
    if not c.startswith("v") and re.match(r"^[\d.]", c):
        c = f"v{c}"
    return f"{pn}_{c}"


def parse_product_core_from_build_string(raw: str | None) -> tuple[str | None, str | None]:
    """
    Split a stored branded string into ``(product_name, core_version)``.

    * ``"GameStar+36_v2.0.0-rc6"`` → ``("GameStar+36", "v2.0.0-rc6")``
    * ``"v2.0.0-rc6"`` (legacy core-only) → ``(None, "v2.0.0-rc6")``
    """
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s or s == PRODUCT_VERSION_PLACEHOLDER or s == "[Version]":
        return None, None
    if "_" in s:
        left, right = s.rsplit("_", 1)
        if _VERSIONISH.match(right):
            core = right if right.startswith("v") else f"v{right}"
            return (left.strip() or None), core
    if s.startswith("v"):
        return None, s
    if re.match(r"^\d+\.\d+", s):
        return None, f"v{s}"
    return None, None
