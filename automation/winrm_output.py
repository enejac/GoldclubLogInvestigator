"""Parse noisy WinRM / Invoke-Command text into operator-readable messages."""

from __future__ import annotations

import re

_SKIP_PREFIXES = (
    "+ ",
    "CategoryInfo",
    "FullyQualifiedErrorId",
    "RemoteException",
    "At line:",
    "At ",
    "+ PSComputerName",
    "+ CategoryInfo",
    "+ FullyQualifiedErrorId",
)
_SKIP_CONTAINS = (
    "PSComputerName",
    "RunspaceId",
    "PSShowComputerName",
)


def meaningful_winrm_lines(text: str, *, limit: int = 8) -> list[str]:
    """Drop remoting table noise; keep script errors and status lines."""
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if any(line.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if any(token in line for token in _SKIP_CONTAINS):
            continue
        if re.match(r"^\+~+", line):
            continue
        out.append(line)
    if limit > 0:
        return out[-limit:]
    return out


def meaningful_winrm_detail(text: str, *, fallback: str = "") -> str:
    lines = meaningful_winrm_lines(text)
    if lines:
        return "\n".join(lines)
    return fallback
