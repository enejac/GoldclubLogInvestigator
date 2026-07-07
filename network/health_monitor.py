"""
Remote physical memory stats via WMIC (``/node``) for correlation with incidents.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

_WMIC_LINE_RE = re.compile(r"^([A-Za-z]+)=(.*)$")


def _parse_wmic_value_block(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _WMIC_LINE_RE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if not val:
            continue
        try:
            out[key] = int(val)
        except ValueError:
            continue
    return out


def _parse_working_set_bytes_sum(text: str) -> int | None:
    """Sum all ``WorkingSetSize`` lines (bytes); multiple ``OneHand.exe`` instances possible."""
    total = 0
    found = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _WMIC_LINE_RE.match(line)
        if not m or m.group(1) != "WorkingSetSize":
            continue
        try:
            total += int(m.group(2).strip())
            found = True
        except ValueError:
            continue
    return total if found else None


def _fetch_onehand_process_mb(ip: str) -> float | None:
    """
    ``WorkingSetSize`` in bytes for ``OneHand.exe`` via remote WMIC.
    Returns MB (rounded), or ``None`` if the process is not found / query fails.
    """
    cmd = [
        "wmic",
        f"/node:{ip}",
        "process",
        "where",
        "name='OneHand.exe'",
        "get",
        "WorkingSetSize",
        "/Value",
    ]
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "timeout": 45,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        r = subprocess.run(cmd, **run_kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("WMIC OneHand process query failed for %s: %s", ip, e)
        return None

    out = r.stdout or ""
    b = _parse_working_set_bytes_sum(out)
    if b is None or b <= 0:
        return None
    return round(b / (1024.0 * 1024.0), 2)


def get_remote_memory_stats(ip_address: str) -> dict[str, Any] | None:
    """
    Query OS RAM (KB) and ``OneHand.exe`` working set (bytes → MB) via remote WMIC.

    Returns ``{"total_mb", "free_mb", "used_mb", "used_pct", "process_mb"}`` or ``None``
    if the OS query fails. ``process_mb`` is ``None`` when the game process is not found.
    """
    ip = (ip_address or "").strip()
    if not ip:
        return None

    if os.name != "nt":
        return None

    cmd = [
        "wmic",
        f"/node:{ip}",
        "OS",
        "get",
        "FreePhysicalMemory,TotalVisibleMemorySize",
        "/Value",
    ]
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "timeout": 45,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        r = subprocess.run(cmd, **run_kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("WMIC memory query failed for %s: %s", ip, e)
        return None

    if r.returncode != 0:
        logger.debug(
            "WMIC non-zero exit for %s: %s %s",
            ip,
            r.stderr,
            r.stdout,
        )
        return None

    vals = _parse_wmic_value_block(r.stdout or "")
    free_kb = vals.get("FreePhysicalMemory")
    total_kb = vals.get("TotalVisibleMemorySize")
    if free_kb is None or total_kb is None or total_kb <= 0:
        return None

    total_mb = total_kb / 1024.0
    free_mb = free_kb / 1024.0
    used_mb = total_mb - free_mb
    if used_mb < 0:
        used_mb = 0.0
    used_pct = (used_mb / total_mb) * 100.0 if total_mb > 0 else 0.0

    process_mb: float | None = _fetch_onehand_process_mb(ip)

    return {
        "total_mb": round(total_mb, 2),
        "free_mb": round(free_mb, 2),
        "used_mb": round(used_mb, 2),
        "used_pct": round(used_pct, 2),
        "process_mb": process_mb,
    }
