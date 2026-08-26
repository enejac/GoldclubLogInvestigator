"""Lightweight display helpers (no SQLAlchemy import chain)."""

from __future__ import annotations


def format_machine_label(name: str | None, ip_address: str | None) -> str:
    """Display ``id (ip)`` when a name/id is known, else the IP alone."""
    ip = (ip_address or "").strip() or "—"
    n = (name or "").strip()
    if n:
        return f"{n} ({ip})"
    return ip