from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


_LINE_RX = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)\s+"
    r"(?P<lvl>[A-Z]+)\s+"
    r"\[(?P<context>[^\]]*)\]\s+"
    r"G:(?P<payload>.+)$"
)


@dataclass(frozen=True, slots=True)
class WinPart:
    """
    One win token from the GameData win field.

    Example token: ``WIN LI35_7[L1W25][L12W10]`` or ``WIN SC30[W30]``.
    """

    raw: str
    kind: str  # e.g. LI / SC / ASC
    code: str  # e.g. LI35_7 / SC30 / ASC40
    total: int | None
    detail: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GameDataSpin:
    """
    Parsed OneHand GameData spin record.

    NOTE: The exact numeric field semantics vary by game; we keep the raw fields and
    extract only what we can rely on for automation validation.
    """

    timestamp: datetime
    level: str
    context: str
    game: str
    spin_id: int | None
    bet_credits: int | None
    raw_fields: tuple[str, ...]
    total_win_credits: int | None
    wins: tuple[WinPart, ...]
    encoded_blob: str | None
    raw_line: str


_WIN_TOKEN_RX = re.compile(r"\bWIN\s+(?P<code>[A-Z]+[0-9_]+)(?P<detail>(?:\[[^\]]+\])*)")
_LINE_WIN_RX = re.compile(r"L(\d+)W(\d+)")
_FEATURE_RX = re.compile(r"FEATURE\[(\d+)\]")


def line_win_credits(win_field: str) -> dict[int, int]:
    """Parse L{n}W{amount} tokens from the GameData win field."""
    return {int(m.group(1)): int(m.group(2)) for m in _LINE_WIN_RX.finditer(win_field or "")}


def sum_reported_win_credits(win_field: str) -> int:
    """Sum line wins and FEATURE[...] amounts from the GameData win field."""
    total = sum(line_win_credits(win_field).values())
    total += sum(int(m.group(1)) for m in _FEATURE_RX.finditer(win_field or ""))
    return total


def _parse_ts(ts: str) -> datetime:
    # Python 3.11+ supports fromisoformat with offsets; keep as aware datetime when offset is present.
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _parse_win_field(field: str) -> tuple[WinPart, ...]:
    if not field or field.strip() in {"|", ""}:
        return ()

    # field contains pipe-separated segments like:
    # "WIN LI5_1[L1W5]|WIN SC30[W30]"
    parts: list[WinPart] = []
    for seg in field.split("|"):
        seg = seg.strip()
        if not seg:
            continue
        m = _WIN_TOKEN_RX.search(seg)
        if not m:
            # Keep unknown as raw for forward compatibility.
            parts.append(WinPart(raw=seg, kind="UNK", code=seg, total=None, detail=()))
            continue
        code = m.group("code")
        kind = re.match(r"[A-Z]+", code).group(0) if re.match(r"[A-Z]+", code) else "UNK"
        detail_str = m.group("detail") or ""
        detail = tuple(x for x in re.findall(r"\[([^\]]+)\]", detail_str) if x)

        # Heuristic: code may embed the total credits, e.g. LI220_2 or SC30.
        total: int | None = None
        num = re.search(r"(\d+)", code)
        if num:
            try:
                total = int(num.group(1))
            except ValueError:
                total = None

        parts.append(WinPart(raw=seg, kind=kind, code=code, total=total, detail=detail))
    return tuple(parts)


def parse_gamedata_line(line: str) -> GameDataSpin | None:
    """
    Parse a single OneHand GameData line.

    Observed stable format (lab .90):
      <ts> <LEVEL> [:thread] G:<game>;<spinId>;<...>;<totalWin>;<...>;<winField>;<blob>
    """

    s = (line or "").rstrip("\r\n")
    m = _LINE_RX.match(s)
    if not m:
        return None

    ts = _parse_ts(m.group("ts"))
    level = m.group("lvl")
    context = m.group("context")
    payload = m.group("payload")

    fields = payload.split(";")
    game = fields[0].strip() if fields else ""

    spin_id: int | None = None
    if len(fields) > 1:
        try:
            spin_id = int(fields[1])
        except ValueError:
            spin_id = None

    bet_credits: int | None = None
    if len(fields) > 2:
        try:
            bet_credits = int(fields[2])
        except ValueError:
            bet_credits = None

    total_win_credits: int | None = None
    if len(fields) > 10:
        try:
            total_win_credits = int(fields[10])
        except ValueError:
            total_win_credits = None

    win_field = fields[12] if len(fields) > 12 else ""
    wins = _parse_win_field(win_field)
    encoded_blob = fields[13] if len(fields) > 13 else None

    return GameDataSpin(
        timestamp=ts,
        level=level,
        context=context,
        game=game,
        spin_id=spin_id,
        bet_credits=bet_credits,
        raw_fields=tuple(fields),
        total_win_credits=total_win_credits,
        wins=wins,
        encoded_blob=encoded_blob,
        raw_line=s,
    )


def iter_spins(lines: Iterable[str]) -> Iterable[GameDataSpin]:
    for line in lines:
        ev = parse_gamedata_line(line)
        if ev is not None:
            yield ev


def most_recent_spins(lines: Iterable[str], *, limit: int = 50) -> list[GameDataSpin]:
    buf: list[GameDataSpin] = []
    for ev in iter_spins(lines):
        buf.append(ev)
        if len(buf) > limit:
            buf.pop(0)
    return buf

