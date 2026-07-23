"""GoldClub mirrors ``config/etc`` and ``bios/etc`` as the same settings tree.

Scans that include both roots would report every change twice. Prefer the
lexicographically highest full relative path (``config/etc/...`` over
``bios/etc/...``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

T = TypeVar("T")


def normalize_rel(relative_path: str) -> str:
    return (relative_path or "").replace("\\", "/").strip("/")


def etc_mirror_key(relative_path: str) -> str | None:
    """Shared key for ``config/etc/X`` and ``bios/etc/X``; else None."""
    low = normalize_rel(relative_path).lower()
    if low.startswith("bios/etc/"):
        return "etc/" + low[len("bios/etc/") :]
    if low.startswith("config/etc/"):
        return "etc/" + low[len("config/etc/") :]
    return None


def highest_full_path(paths: Iterable[str]) -> str:
    """Return the lexicographically highest normalized relative path."""
    items = list(paths)
    if not items:
        raise ValueError("highest_full_path requires at least one path")
    return max(items, key=lambda p: normalize_rel(p).lower())


def select_highest_etc_mirror_paths(relative_paths: Iterable[str]) -> list[str]:
    """Keep non-mirrors as-is; for each etc mirror pair keep the highest path."""
    by_key: dict[str, list[str]] = {}
    others: list[str] = []
    for rel in relative_paths:
        key = etc_mirror_key(rel)
        if key is None:
            others.append(rel)
            continue
        by_key.setdefault(key, []).append(rel)

    kept = list(others)
    for paths in by_key.values():
        kept.append(highest_full_path(paths))
    return sorted(kept, key=lambda p: normalize_rel(p).lower())


def dedupe_etc_mirror_items(
    items: Iterable[T],
    *,
    path_of,
    fingerprint_of=None,
) -> list[T]:
    """Collapse bios/etc ↔ config/etc duplicates, keeping the highest path.

    When mirror siblings share the same fingerprint (or fingerprint_of is None),
    only the highest full path is kept. Divergent fingerprints are all kept
    (each group's highest path).
    """
    by_key: dict[str, list[T]] = {}
    others: list[T] = []
    for item in items:
        rel = path_of(item)
        key = etc_mirror_key(rel)
        if key is None:
            others.append(item)
            continue
        by_key.setdefault(key, []).append(item)

    out = list(others)
    for group in by_key.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        if fingerprint_of is None:
            out.append(max(group, key=lambda item: normalize_rel(path_of(item)).lower()))
            continue
        by_fp: dict[object, list[T]] = {}
        for item in group:
            by_fp.setdefault(fingerprint_of(item), []).append(item)
        for siblings in by_fp.values():
            out.append(
                max(siblings, key=lambda item: normalize_rel(path_of(item)).lower())
            )
    return sorted(out, key=lambda item: normalize_rel(path_of(item)).lower())
