"""Wall-clock date for snapshot folder names.

Lab cabinets often run with the OS clock rolled back (Ruleta trial / ERROR 30).
datetime.now() then names a scan 2026-08-11 on a real August 21.
Use a later reference date (exe link time, file mtime, HTTP Date) when the
local calendar day is behind.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def apply_snapshot_clock(
    local: datetime,
    *,
    live_now: datetime | None = None,
    date_floors: list[datetime] | None = None,
) -> datetime:
    """Keep local time-of-day; move the date forward when references are later.

    live_now (HTTP / peer) is used in full when its calendar day is ahead.
    date_floors only supply a later date (exe mtime / PE stamp).
    """
    here = local if local.tzinfo is not None else local.astimezone()
    if live_now is not None:
        live = live_now.astimezone(here.tzinfo)
        if live.date() > here.date():
            return live
    best = here.date()
    for ref in date_floors or ():
        if ref is None:
            continue
        if ref.tzinfo is not None:
            day = ref.astimezone(here.tzinfo).date()
        else:
            day = ref.date()
        if day > best:
            best = day
    if best == here.date():
        return here
    return here.replace(year=best.year, month=best.month, day=best.day)


def pe_link_datetime(path: Path) -> datetime | None:
    """COFF TimeDateStamp from a PE image, or None if missing / nonsense."""
    try:
        with Path(path).open("rb") as handle:
            header = handle.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return None
            e_lfanew = int.from_bytes(header[0x3C:0x40], "little")
            if e_lfanew < 64:
                return None
            handle.seek(e_lfanew)
            coff = handle.read(12)
    except OSError:
        return None
    if len(coff) < 12 or coff[:4] != b"PE\0\0":
        return None
    stamp = int.from_bytes(coff[8:12], "little")
    # Ignore 0 / reproducible-build hashes that are not plausible Unix times.
    if stamp < 1_600_000_000 or stamp > 2_200_000_000:
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone()


def file_mtime_datetime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime).astimezone()
    except OSError:
        return None


def http_date_now(*, timeout: float = 0.8) -> datetime | None:
    """Parse a Date header. Fail closed on timeout or offline cabinets."""
    req = Request(
        "http://1.1.1.1/",
        method="HEAD",
        headers={"User-Agent": "LogInvestigator-snapshot-clock"},
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.headers.get("Date")
    except (OSError, URLError, ValueError, TimeoutError):
        return None
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def _clock_anchor_files() -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        key = str(resolved).casefold()
        if key in seen or not resolved.is_file():
            return
        seen.add(key)
        paths.append(resolved)

    if getattr(sys, "frozen", False):
        _add(Path(sys.executable))
    else:
        repo = Path(__file__).resolve().parents[1]
        _add(repo / "dist" / "LogInvestigator.exe")
        _add(repo / "LogInvestigator.exe")
        _add(Path(__file__))
    return paths


def collect_date_floors() -> list[datetime]:
    floors: list[datetime] = []
    for path in _clock_anchor_files():
        mtime = file_mtime_datetime(path)
        if mtime is not None:
            floors.append(mtime)
        linked = pe_link_datetime(path)
        if linked is not None:
            floors.append(linked)
    return floors


def resolve_snapshot_datetime(
    *,
    local: datetime | None = None,
    live_now: datetime | None = None,
    date_floors: list[datetime] | None = None,
    probe_network: bool = True,
) -> datetime:
    """Datetime used for snapshot folder names and scanTimestamp."""
    here = local if local is not None else datetime.now().astimezone()
    live = live_now
    if live is None and probe_network:
        live = http_date_now()
    floors = date_floors if date_floors is not None else collect_date_floors()
    return apply_snapshot_clock(here, live_now=live, date_floors=floors)
