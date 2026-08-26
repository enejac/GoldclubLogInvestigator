"""
Prove a mapped box by its corners, not its middle.

A hitbox that is a few pixels too big still passes a centre-click test, and the
error only shows up later as a bet placed on the neighbouring cell. So every
control is clicked twice - one pixel inside its top-left corner and one pixel
inside its bottom-right corner - and the two clicks have to produce the **same**
event. Anything else means the box does not describe one control:

  ``same``          both corners produced the same event: the box is sound
  ``differs``       the corners hit two different controls: the box spans a border
  ``top_left``      only the top-left corner registered: the box is too big
  ``bottom_right``  only the bottom-right corner registered: likewise
  ``silent``        neither corner produced anything: wrong place, or inert

What counts as "the same event" depends on what the control talks to:

*bets*    ``PlayerDataBets`` from the middleware (``:8090``) names the bet the
          core actually booked, so the comparison is exact and needs no logs.
          It also has to be exact, because the client does **not** log bets:
          ``PlaceBet`` and ``CancelAllBets`` never reach the godot log on
          ``.90``, so a log tail watching a bet sweep sees nothing at all and
          says nothing about whether the clicks landed. The middleware read is
          the only bet evidence there is.
*chrome*  the ``Sending put action ...`` lines the client writes to its godot
          log - which do cover ``MenuCommands``, ``SetChip``, ``Paytable`` and
          ``SetNeighboursPower`` - with a screenshot diff as the fallback for
          controls that only redraw.

Speed comes from two things. Bet spots whose Id the registry already knows are
probed in batches - one script clicks eight corners, one read names every bet
that landed - and the betting window is tracked rather than waited for, so
several probes share the 22 s it is open. Every cloth bet is cancelled inside
the window it was placed in, so a full sweep costs nothing.

Runs against a cabinet over the share or against this machine
(``--target local``); see :mod:`automation.roulette_target`.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from app_paths import app_tmp_logs_dir
from automation.roulette_layout_store import load_hitboxes, parse_hitbox_area
from automation.roulette_surface import DEFAULT_LAYOUT, surface_for
from automation.roulette_target import Session, Target, open_session, parse_target

ProgressFn = Callable[[str], None]

# One pixel in from the drawn edge: the smallest step that is still inside the
# box. A control that fails at 1 px and passes at 2 px is over-sized by exactly
# the amount worth reporting.
INSET_PX = 1
# A pixel diff this large means the screen reacted to the click.
CHANGE_FRACTION = 0.0004
PIXEL_DELTA = 26
# How many number spots share one read. Eight clicks and a read fit inside the
# betting window with room to spare.
BATCH = 8
# A bet that is refused for want of credit looks exactly like a missed click, so
# the sweep stops probing bets below this balance (base units) and says why.
CREDIT_FLOOR = 100_000
# Measured on .90: betting is open 22 s out of a 40 s cycle.
WINDOW_SECONDS = 22.0

# "Sending put action PlaceBet data [\"17\"]" - the client telling the middleware
# what the operator just touched.
PUT_RE = re.compile(
    r"Sending put action\s+(?P<action>[A-Za-z]+)(?:\s+data\s+(?P<data>.*))?",
    re.IGNORECASE,
)
OPEN_RE = re.compile(r"Bets are open", re.IGNORECASE)
CLOSING_RE = re.compile(r"Bets are clos", re.IGNORECASE)

# Controls that must never be clicked by a sweep: one locks the cabinet behind a
# PIN nobody here knows, two raise a service call that cannot be recalled, and the
# skin selectors would swap the cloth out from under every remaining control - the
# registry being swept describes one skin only.
NEVER_CLICK = {
    "MENU_PIN_LOCK",
    "MENU_ASSISTANT",
    "LLAMAR",
    "CALL_ATTENDANT",
    "PANO",
    "LAYOUT_SWITCH",
}

# The racetrack's pockets and the small cloth inside it only exist on the other
# view, so they are probed as their own pass with the view switched first.
RACE_FAMILIES = frozenset({"race", "mini"})

# A sweep that keeps failing the same way is reporting on its own plumbing, not on
# the mapping, so it stops instead of filling the pack with identical errors.
MAX_CONSECUTIVE_ERRORS = 3

# Outside bets are dropped silently below the cabinet's outside minimum (~10
# credits), which looks exactly like a missed click. They stay out of the sweep
# unless asked for by name, and the report says so rather than inventing failures.
LOW_STAKE_FAMILIES = {"outside"}

BET_FAMILIES = ("straight", "outside", "inside", "race", "mini")
ALL_FAMILIES = BET_FAMILIES + ("chrome",)


def out_root() -> Path:
    return app_tmp_logs_dir() / "edge_probe"


def out_dir_for(layout_id: str, target: Target) -> Path:
    return out_root() / f"{layout_id}_{target.key}"


# ---------------------------------------------------------------------------
# What to probe
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Spot:
    """One mapped control and the two pixels that will be clicked."""

    button_id: str
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 (far edge exclusive)
    family: str  # straight | outside | inside | race | mini | chrome
    expect: str = ""  # bet Id the middleware should report, when it is knowable

    @property
    def top_left(self) -> tuple[int, int]:
        return (self.box[0] + INSET_PX, self.box[1] + INSET_PX)

    @property
    def bottom_right(self) -> tuple[int, int]:
        return (self.box[2] - 1 - INSET_PX, self.box[3] - 1 - INSET_PX)

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1]

    @property
    def too_small(self) -> bool:
        """A box this thin cannot carry two distinct inset corners."""
        return self.width < 2 * INSET_PX + 3 or self.height < 2 * INSET_PX + 3

    @property
    def is_bet(self) -> bool:
        return self.family in BET_FAMILIES

    def corner(self, which: str) -> tuple[int, int]:
        return self.top_left if which == "top_left" else self.bottom_right


def _family_of(entry: dict[str, Any], button_id: str) -> str:
    kind = str(entry.get("kind") or "").lower()
    bid = button_id.lower()
    if bid.startswith("race_"):
        return "race"
    if bid.startswith("mini_"):
        return "mini"
    if kind in ("overlay", "ui", "chrome") or entry.get("overlay"):
        return "chrome"
    inside_prefixes = ("split_", "street_", "corner_", "sixline_", "basket", "trio")
    if any(bid.startswith(p) for p in inside_prefixes):
        return "inside"
    if bid.isdigit() or bid in ("0", "00"):
        return "straight"
    return "outside" if kind == "bet" else "chrome"


def expected_id(entry: dict[str, Any], button_id: str, family: str) -> str:
    """
    The bet Id the middleware should report for this spot.

    Verified spots carry it as ``expect.Id``, written by the bet sweep that proved
    them; unproven ones still carry the ``place_bet`` string they were mapped
    from. Knowing the Id up front is what lets a spot share a read with others.
    """
    if family not in BET_FAMILIES:
        return ""
    expect = entry.get("expect")
    if isinstance(expect, dict) and expect.get("Id"):
        return str(expect["Id"])
    place = entry.get("place_bet")
    if isinstance(place, list) and len(place) == 1 and isinstance(place[0], str):
        return place[0]
    tail = button_id.rsplit("_", 1)[-1]
    if tail == "00":
        return "37"
    return tail if tail.isdigit() else ""


# A six-line covers six numbers and is the widest bet that still costs one chip;
# every outside bet covers twelve or more.
CHIP_BET_MAX_NUMBERS = 6


def pays_chip_stake(button_id: str, expect: str) -> bool:
    """
    Does one click on this spot cost a single chip?

    Named bets - EVEN, ROJO, 1-12, 2 a 1 and their racetrack twins - are dropped
    silently below the cabinet's outside minimum, which is indistinguishable from
    a missed click, so they have to be recognised however they happen to be drawn.
    The registry names them by the numbers they cover, so the count is the test;
    spots that carry no Id at all are judged by their own name.
    """
    if expect:
        parts = [p for p in str(expect).split("+") if p]
        return bool(parts) and len(parts) <= CHIP_BET_MAX_NUMBERS and all(p.isdigit() for p in parts)
    tail = button_id.rsplit("_", 1)[-1]
    return tail.isdigit() or tail == "00"

def collect_spots(
    layout_id: str = DEFAULT_LAYOUT,
    *,
    families: Sequence[str] = (),
    only: Sequence[str] = (),
) -> list[Spot]:
    """
    Every mapped control worth clicking, read from the registry.

    The registry is the source rather than the Python geometry, because it is what
    a mapping run updates and therefore what needs proving after one.
    """
    buttons = load_hitboxes(layout_id).get("buttons") or {}
    surface = surface_for(layout_id)
    forbidden = set(getattr(surface, "forbidden", ()) or ()) | NEVER_CLICK
    wanted = {b.strip() for b in only if b.strip()}
    want_families = {f.strip().lower() for f in families if f.strip()}

    spots: list[Spot] = []
    for bid, entry in sorted(buttons.items()):
        if not isinstance(entry, dict) or bid in forbidden:
            continue
        if wanted and bid not in wanted:
            continue
        family = _family_of(entry, bid)
        expect = expected_id(entry, bid, family)
        if family in BET_FAMILIES and not pays_chip_stake(bid, expect):
            family = "outside"
        if want_families:
            if family not in want_families:
                continue
        elif family in LOW_STAKE_FAMILIES and not wanted:
            continue
        area = parse_hitbox_area({**entry, "id": bid}, layout_id)
        if area is None:
            continue
        box = (area.x, area.y, area.x + area.width, area.y + area.height)
        spots.append(Spot(bid, box, family, expect=expect))
    return spots


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def bet_signature(bets: Iterable[Any]) -> str:
    """A stable name for whatever is on the cloth, so two clicks can be compared."""
    parts: list[str] = []
    for bet in bets or []:
        if not isinstance(bet, dict):
            continue
        parts.append(f"{bet.get('BetType') or '?'}:{bet.get('Id')}")
    return "+".join(sorted(parts))


def bet_for(bets: Sequence[Any], expect: str) -> str:
    """The one bet entry matching an expected Id, as a signature string."""
    for bet in bets or []:
        if isinstance(bet, dict) and str(bet.get("Id")) == str(expect):
            return f"{bet.get('BetType') or '?'}:{bet.get('Id')}"
    return ""


def put_signature(lines: Iterable[str]) -> str:
    """The middleware calls a click produced, as one comparable string."""
    seen: list[str] = []
    for line in lines:
        m = PUT_RE.search(line)
        if not m:
            continue
        action = m.group("action")
        data = (m.group("data") or "").strip()
        seen.append(f"{action}({data})" if data else action)
    return "+".join(seen)


def latest_log(session: Session, name: str = "godot1") -> Path | None:
    """Newest file in one of the game's log folders, or None if out of reach."""
    folder = session.log_dir(name)
    try:
        files = [p for p in folder.iterdir() if p.is_file()]
    except OSError:
        return None
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def log_size(path: Path | None) -> int:
    if path is None:
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def read_since(path: Path | None, offset: int, *, limit: int = 400_000) -> list[str]:
    """Lines appended to *path* since *offset*, tolerant of the writer's lock."""
    if path is None:
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, offset))
            data = fh.read(limit)
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()


class BetWindow:
    """
    Tracks whether the cloth is taking bets right now.

    Waiting for a fresh "Bets are open" line before every probe would spend most
    of a sweep asleep, because the marker only comes round every 40 s. Instead the
    phase is followed as the log grows and a probe goes ahead whenever enough of
    the current window is left for it, which fits several probes into one window.
    """

    def __init__(self, session: Session, *, progress: ProgressFn = lambda _l: None) -> None:
        self.log = latest_log(session, "ruleta Roulette")
        self.offset = log_size(self.log)
        self.progress = progress
        self.opened_at = 0.0
        self.closed = True
        self.blind = self.log is None
        if self.blind:
            progress("  no ruleta log in reach; the betting-window gate is off")

    def _drain(self) -> None:
        for line in read_since(self.log, self.offset):
            if OPEN_RE.search(line):
                self.opened_at = time.time()
                self.closed = False
            elif CLOSING_RE.search(line):
                self.closed = True
        self.offset = log_size(self.log)

    def remaining(self) -> float:
        if self.closed or not self.opened_at:
            return 0.0
        return max(0.0, WINDOW_SECONDS - (time.time() - self.opened_at))

    def ensure(self, *, need: float = 8.0, timeout: float = 90.0) -> bool:
        """Return once at least *need* seconds of betting time are left."""
        if self.blind:
            return True
        deadline = time.time() + timeout
        while True:
            self._drain()
            if self.remaining() >= need:
                return True
            if time.time() >= deadline:
                self.progress("  betting window never opened wide enough; probing anyway")
                return False
            time.sleep(0.6)


def click_changes_screen(
    session: Session,
    point: tuple[int, int],
    *,
    layout_id: str = DEFAULT_LAYOUT,
    settle_ms: int = 700,
) -> tuple[bool, float]:
    """Click *point* and report whether the picture changed, and by how much."""
    import numpy as np
    from PIL import Image

    work = out_root() / "_pixel"
    work.mkdir(parents=True, exist_ok=True)
    before = work / "before.png"
    after = work / "after.png"
    ok, detail = session.capture(before, settle_ms=200)
    if not ok:
        raise RuntimeError(f"capture failed: {detail}")
    ok, detail = session.capture(after, steps=[session.pixel_step(*point)], settle_ms=settle_ms)
    if not ok:
        raise RuntimeError(f"click/capture failed: {detail}")
    a = np.asarray(Image.open(before).convert("RGB"), dtype=np.int16)
    b = np.asarray(Image.open(after).convert("RGB"), dtype=np.int16)
    if a.shape != b.shape:
        return True, 1.0
    changed = (np.abs(a - b).max(axis=2) > PIXEL_DELTA).mean()
    return bool(changed > CHANGE_FRACTION), float(changed)


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


@dataclass
class CornerHit:
    """What one corner click produced."""

    corner: str
    point: tuple[int, int]
    signature: str = ""
    detail: str = ""
    ok: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "corner": self.corner,
            "point": list(self.point),
            "signature": self.signature,
            "detail": self.detail,
            "ok": self.ok,
        }


@dataclass
class EdgeVerdict:
    """The comparison of one control's two corners."""

    button_id: str
    family: str
    box: tuple[int, int, int, int]
    verdict: str
    oracle: str
    hits: list[CornerHit] = field(default_factory=list)
    note: str = ""
    suggested_box: tuple[int, int, int, int] | None = None

    @property
    def ok(self) -> bool:
        return self.verdict in ("same", "skipped")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.button_id,
            "family": self.family,
            "box": list(self.box),
            "verdict": self.verdict,
            "oracle": self.oracle,
            "ok": self.ok,
            "note": self.note,
            "corners": [h.as_dict() for h in self.hits],
            "suggested_box": list(self.suggested_box) if self.suggested_box else None,
        }


def compare_corners(
    spot: Spot, first: CornerHit, second: CornerHit, *, oracle: str
) -> EdgeVerdict:
    """
    Turn two corner results into a verdict, plus a smaller box when one missed.

    The suggestion is deliberately conservative - pull the failing edge in by two
    pixels rather than guess where the real border is - because the probe knows
    that an edge is wrong, not where it should have been.
    """
    a, b = first.signature, second.signature
    verdict = "silent"
    note = ""
    suggested: tuple[int, int, int, int] | None = None
    x0, y0, x1, y1 = spot.box

    if a and b and a == b:
        verdict = "same"
    elif a and b:
        verdict = "differs"
        note = f"top-left gave {a}, bottom-right gave {b}"
    elif a:
        verdict = "bottom_right"
        note = f"top-left gave {a}, bottom-right produced nothing"
        suggested = (x0, y0, max(x0 + 3, x1 - 2), max(y0 + 3, y1 - 2))
    elif b:
        verdict = "top_left"
        note = f"bottom-right gave {b}, top-left produced nothing"
        suggested = (min(x1 - 3, x0 + 2), min(y1 - 3, y0 + 2), x1, y1)
    else:
        note = "neither corner produced an event"

    if spot.expect and verdict == "same" and spot.expect not in a:
        verdict = "differs"
        note = f"both corners gave {a}, but this spot should report Id={spot.expect}"

    return EdgeVerdict(
        button_id=spot.button_id,
        family=spot.family,
        box=spot.box,
        verdict=verdict,
        oracle=oracle,
        hits=[first, second],
        note=note,
        suggested_box=suggested,
    )


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def arm_chip(session: Session, *, index: int = 0, progress: ProgressFn = lambda _l: None) -> bool:
    """Select the smallest chip, or a cloth click books nothing at all."""
    res = session.put("SetChip", str(index))
    ok = bool(res.get("success") or res.get("ok"))
    progress(f"  chip {index}: {'armed' if ok else res.get('error') or 'not accepted'}")
    return ok


def view_for(spot: Spot) -> str:
    """Which cloth this control lives on."""
    return "race" if spot.family in RACE_FAMILIES else "square"


def missing_oracles(session: Session, spots: Sequence[Spot], *, pixel: bool = False) -> list[str]:
    """
    What this sweep would have no evidence for. Empty means it can start.

    Worth the two round trips: a cabinet that has been reimaged for another game
    still answers SMB, so without this the sweep clicks for an hour and reports
    every control as ``silent`` - which reads like a mapping catastrophe and is
    really an empty cabinet.
    """
    gaps: list[str] = []
    if any(s.is_bet for s in spots):
        state = session.player_state()
        if not state.get("ok"):
            why = str(state.get("error") or "no answer")[:160]
            gaps.append(f"the middleware is not answering, so no bet can be proved ({why})")
    if any(not s.is_bet for s in spots) and not pixel and latest_log(session) is None:
        gaps.append(
            f"there is no godot log under {session.log_dir('godot1').parent}, so a chrome "
            "click has nothing to be judged by - pass --pixel to use screenshots instead"
        )
    return gaps


class BetCreditExhausted(RuntimeError):
    """Raised when the balance is too low to tell a refused bet from a miss."""

    def __init__(self, credit: int) -> None:
        super().__init__(
            f"balance {credit} base units is under the {CREDIT_FLOOR} floor: "
            "a refused bet is indistinguishable from a missed click, so bet "
            "spots are left unprobed. Top up and re-run."
        )
        self.credit = credit


def bet_credit(session: Session) -> int | None:
    """The cabinet's balance in base units, or None when it cannot be read."""
    state = session.player_state()
    if not state.get("ok"):
        return None
    try:
        return int(state.get("credits") or 0)
    except (TypeError, ValueError):
        return None


def probe_bet_spot(
    session: Session,
    spot: Spot,
    *,
    window: BetWindow,
    progress: ProgressFn = lambda _l: None,
) -> EdgeVerdict:
    """
    Click one corner, read the cloth, clear it, then do the other corner.

    Reading and cancelling in a single round trip keeps a corner's bet inside the
    window it was placed in, so the pair is compared under the same conditions.
    """
    hits: list[CornerHit] = []
    for corner in ("top_left", "bottom_right"):
        point = spot.corner(corner)
        window.ensure(need=8.0)
        ok, detail = session.run([session.pixel_step(*point)])
        if not ok:
            session.player_state(cancel=True)
            hits.append(CornerHit(corner, point, detail=detail[:200], ok=False))
            continue
        state = session.player_state(cancel=True)
        hits.append(
            CornerHit(
                corner,
                point,
                signature=bet_signature(state.get("bets") or []),
                detail=str(state.get("error") or ""),
            )
        )
    return compare_corners(spot, hits[0], hits[1], oracle="middleware")


def probe_bet_batch(
    session: Session,
    spots: Sequence[Spot],
    *,
    window: BetWindow,
    progress: ProgressFn = lambda _l: None,
) -> list[EdgeVerdict]:
    """
    Probe several bet spots in two round trips instead of two per spot.

    Every spot here has a known Id, so one script can click all their top-left
    corners and one read names every bet that landed. If the number of bets does
    not match the number of identified clicks the batch is ambiguous - a click may
    have landed on a neighbour another spot in the same batch claims - and the
    whole batch is re-probed singly rather than reported on a guess.
    """
    if not spots:
        return []
    if len(spots) == 1:
        return [probe_bet_spot(session, spots[0], window=window, progress=progress)]

    found: dict[str, dict[str, str]] = {s.button_id: {} for s in spots}
    for corner in ("top_left", "bottom_right"):
        window.ensure(need=11.0)
        steps = [session.pixel_step(*s.corner(corner), ms=140) for s in spots]
        ok, detail = session.run(steps, timeout=150)
        state = session.player_state(cancel=True)
        if not ok:
            progress(f"  batch click failed ({detail[:120]}); probing these singly")
            return [probe_bet_spot(session, s, window=window, progress=progress) for s in spots]
        bets = state.get("bets") or []
        for spot in spots:
            found[spot.button_id][corner] = bet_for(bets, spot.expect)
        landed = sum(1 for s in spots if found[s.button_id][corner])
        if len(bets) != landed:
            progress(
                f"  {corner}: {len(bets)} bets for {landed} identified clicks - "
                "something landed unclaimed, re-probing this batch singly"
            )
            return [probe_bet_spot(session, s, window=window, progress=progress) for s in spots]

    out: list[EdgeVerdict] = []
    for spot in spots:
        sig = found[spot.button_id]
        out.append(
            compare_corners(
                spot,
                CornerHit("top_left", spot.top_left, signature=sig.get("top_left", "")),
                CornerHit(
                    "bottom_right", spot.bottom_right, signature=sig.get("bottom_right", "")
                ),
                oracle="middleware",
            )
        )
    return out


def probe_chrome(
    session: Session,
    spot: Spot,
    *,
    log: Path | None,
    progress: ProgressFn = lambda _l: None,
    settle_ms: int = 900,
    pixel: bool = False,
) -> EdgeVerdict:
    """
    Click one corner, read what the client told the middleware, then the other.

    Chrome has no middleware ground truth of its own, so the godot log's
    ``Sending put action`` lines are the event. Controls that only redraw can be
    told apart from a missed click by a screenshot diff, which is what *pixel*
    turns on - but it costs three cabinet screenshots per control, and the
    client logs only a handful of action names, so most of the chrome falls into
    that path. Off by default: the sweep says "no evidence channel" instead of
    spending an hour proving it.
    """
    hits: list[CornerHit] = []
    for corner in ("top_left", "bottom_right"):
        point = spot.corner(corner)
        offset = log_size(log)
        ok, detail = session.run([session.pixel_step(*point), {"type": "sleep", "ms": settle_ms}])
        if not ok:
            hits.append(CornerHit(corner, point, detail=detail[:200], ok=False))
            continue
        time.sleep(0.4)
        hits.append(CornerHit(corner, point, signature=put_signature(read_since(log, offset))))

    verdict = compare_corners(spot, hits[0], hits[1], oracle="godot_put")
    if verdict.verdict != "silent":
        return verdict
    if not pixel:
        verdict.note = (
            f"{verdict.note}; no logged action for this control - re-run with "
            "--pixel to tell a redraw from a missed click"
        ).lstrip("; ")
        return verdict
    try:
        changed_a, delta_a = click_changes_screen(session, spot.top_left)
        changed_b, delta_b = click_changes_screen(session, spot.bottom_right)
    except RuntimeError as exc:
        verdict.note = f"{verdict.note}; pixel fallback failed: {exc}"
        return verdict
    return compare_corners(
        spot,
        CornerHit(
            "top_left", spot.top_left, signature=f"redraw({delta_a:.4f})" if changed_a else ""
        ),
        CornerHit(
            "bottom_right",
            spot.bottom_right,
            signature=f"redraw({delta_b:.4f})" if changed_b else "",
        ),
        oracle="pixel_diff",
    )


def plan_batches(spots: Sequence[Spot], *, batch: int = BATCH) -> list[list[Spot]]:
    """
    Group the sweep into units of work: batched bet spots, then singles.

    Only spots with a known Id can share a read, and neighbours are kept apart by
    taking every *n*-th spot, so a click that slips one cell sideways lands on
    something no other spot in the same batch claims.
    """
    batchable = [s for s in spots if s.is_bet and s.expect and not s.too_small]
    rest = [s for s in spots if s not in batchable]
    # Chrome last, and the chip buttons last of all: clicking `chip_100` arms a
    # 100-credit stake, and any bet probed after that stakes 100 a number.
    singles = [s for s in rest if s.is_bet]
    singles += [s for s in rest if not s.is_bet and not s.button_id.startswith("chip_")]
    singles += [s for s in rest if not s.is_bet and s.button_id.startswith("chip_")]
    groups: list[list[Spot]] = []
    if batch > 1 and batchable:
        stride = max(1, (len(batchable) + batch - 1) // batch)
        spread: list[Spot] = []
        for start in range(stride):
            spread.extend(batchable[start::stride])
        groups = [spread[i : i + batch] for i in range(0, len(spread), batch)]
    else:
        groups = [[s] for s in batchable]
    groups.extend([s] for s in singles)
    return groups


def probe_view(session: Session, layout_id: str) -> str:
    """Which cloth the target is showing: ``square``, ``race`` or ``unknown``."""
    from automation.roulette_verify_ui import _load, current_view

    work = out_root() / "_view"
    work.mkdir(parents=True, exist_ok=True)
    shot = work / "view_check.png"
    ok, detail = session.capture(shot, settle_ms=1600)
    if not ok:
        raise RuntimeError(f"view capture failed: {detail}")
    return current_view(_load(shot), layout_id)


def ensure_probe_view(
    session: Session,
    layout_id: str,
    want: str,
    *,
    window: BetWindow | None = None,
    attempts: int = 3,
    progress: ProgressFn = lambda _l: None,
) -> bool:
    """
    Put the target on the cloth *want* and prove it with a capture.

    ``CAMBIAR VISTA`` only answers while the betting window is open and the swap
    animates for about a second, so each attempt waits for the window and lets the
    capture's own settle cover the animation.
    """
    seen = probe_view(session, layout_id)
    if seen == want:
        return True
    boxes = load_hitboxes(layout_id).get("buttons") or {}
    entry = boxes.get("CHANGE_VIEW")
    area = parse_hitbox_area({**entry, "id": "CHANGE_VIEW"}, layout_id) if entry else None
    if area is None:
        progress(f"  no CHANGE_VIEW in the {layout_id} registry; cannot reach the {want} view")
        return False
    point = (area.x + area.width // 2, area.y + area.height // 2)
    for _ in range(max(1, attempts)):
        if window is not None:
            window.ensure(need=4.0, timeout=45.0)
        session.run([session.pixel_step(*point, ms=140)])
        seen = probe_view(session, layout_id)
        progress(f"  view: wanted {want}, now {seen}")
        if seen == want:
            return True
    return False


def probe_spots(
    session: Session,
    spots: Sequence[Spot],
    *,
    layout_id: str = DEFAULT_LAYOUT,
    batch: int = BATCH,
    pixel: bool = False,
    checkpoint: Callable[[Sequence[EdgeVerdict]], None] | None = None,
    progress: ProgressFn = print,
) -> list[EdgeVerdict]:
    """Probe every spot: bets through the middleware, chrome through the log."""
    log = latest_log(session, "godot1")
    if log is None:
        progress("  no godot log in reach; chrome controls fall back to pixel evidence")
    window = BetWindow(session, progress=progress)
    if any(s.is_bet for s in spots):
        arm_chip(session, progress=progress)

    out: list[EdgeVerdict] = []
    done = 0
    total = len(spots)
    broke: int | None = None
    misfires = 0

    def note_all(part: Sequence[Spot], verdict: str, note: str) -> None:
        nonlocal done
        for spot in part:
            done += 1
            out.append(
                EdgeVerdict(spot.button_id, spot.family, spot.box, verdict, "none", note=note)
            )

    for view in ("square", "race"):
        part = [s for s in spots if view_for(s) == view]
        if not part or misfires >= MAX_CONSECUTIVE_ERRORS:
            continue
        if view == "race" and not ensure_probe_view(
            session, layout_id, "race", window=window, progress=progress
        ):
            note_all(
                part,
                "skipped",
                "the target would not switch to the racetrack view, where this control "
                "lives; clicking it on the square cloth would have booked whatever is "
                "under those pixels instead",
            )
            progress(f"[{done}/{total}] -- {len(part)} racetrack controls skipped: wrong view")
            continue

        for group in plan_batches(part, batch=batch):
            heads: list[Spot] = []
            for spot in group:
                if spot.too_small:
                    note_all(
                        [spot],
                        "skipped",
                        f"box is {spot.width}x{spot.height} px: too small for two corners",
                    )
                    progress(f"[{done}/{total}] -- {spot.button_id}: skipped (box too small)")
                else:
                    heads.append(spot)
            if not heads:
                continue
            try:
                if all(s.is_bet for s in heads):
                    if broke is None:
                        credit = bet_credit(session)
                        if credit is not None and credit < CREDIT_FLOOR:
                            broke = credit
                    if broke is not None:
                        raise BetCreditExhausted(broke)
                    # Re-armed per unit, not once per sweep: a chip button probed
                    # earlier, or a stake the cabinet remembers, would otherwise
                    # decide what every later bet costs.
                    arm_chip(session)
                    verdicts = probe_bet_batch(session, heads, window=window, progress=progress)
                else:
                    verdicts = [
                        probe_chrome(session, s, log=log, pixel=pixel, progress=progress)
                        for s in heads
                    ]
                misfires = 0
            except BetCreditExhausted as exc:
                verdicts = [
                    EdgeVerdict(s.button_id, s.family, s.box, "skipped", "none", note=str(exc))
                    for s in heads
                ]
            except Exception as exc:  # noqa: BLE001 -- one bad control must not end the sweep
                misfires += 1
                verdicts = [
                    EdgeVerdict(s.button_id, s.family, s.box, "error", "none", note=str(exc)[:200])
                    for s in heads
                ]
            for verdict in verdicts:
                done += 1
                mark = "OK " if verdict.ok else "BAD"
                progress(
                    f"[{done}/{total}] {mark} {verdict.button_id} {verdict.verdict}"
                    + (f" - {verdict.note}" if verdict.note else "")
                )
            out.extend(verdicts)
            if checkpoint is not None:
                # A sweep of the whole cloth takes long enough that losing it to a
                # dropped share or a closed session would be worse than the run.
                checkpoint(out)
            if misfires >= MAX_CONSECUTIVE_ERRORS:
                rest = [s for s in part if not any(v.button_id == s.button_id for v in out)]
                note_all(
                    rest,
                    "skipped",
                    f"stopped after {misfires} units failed the same way: the sweep was "
                    "reporting on its own plumbing, not on the mapping",
                )
                progress(
                    f"  {misfires} units failed in a row; stopping with "
                    f"{len(rest)} controls unprobed"
                )
                break
    return out


MARGIN_LADDER = (3, 6, 10, 16, 24)


def clean_hit(bets: Sequence[Any], expect: str) -> bool:
    """
    Did *only* the intended bet land, with no wider bet that also covers it?

    A click near a cell border books the split, street or corner that shares that
    border instead of the number, and those are reported as ``17+20`` style ids. So
    the number appearing is not enough: any wider bet containing it means the click
    was still inside the border band.
    """
    got = False
    for bet in bets or []:
        if not isinstance(bet, dict):
            continue
        ids = str(bet.get("Id") or "")
        if ids == str(expect):
            got = True
        elif str(expect) in ids.split("+"):
            return False
    return got


def measure_corner_margin(
    session: Session,
    spots: Sequence[Spot],
    *,
    window: BetWindow,
    ladder: Sequence[int] = MARGIN_LADDER,
    progress: ProgressFn = lambda _l: None,
) -> dict[str, int | None]:
    """
    How far inside its own box does each corner have to move to book the number?

    Walks both corners inwards together and stops at the first inset where the read
    shows the number and nothing wider. Spots drop out as they pass, so the cost is
    one round trip per remaining batch per rung rather than per control. The answer
    is a real measurement of the border band the game reserves for split bets,
    which is what makes a suggested box worth looking at.
    """
    todo = {s.button_id: s for s in spots}
    found: dict[str, int | None] = {s.button_id: None for s in spots}
    for inset in ladder:
        if not todo:
            break
        wide = [s for s in todo.values() if s.width > 2 * inset + 2 and s.height > 2 * inset + 2]
        for spot in [s for s in todo.values() if s not in wide]:
            todo.pop(spot.button_id, None)
        for group in plan_batches(wide, batch=BATCH):
            window.ensure(need=11.0)
            steps: list[dict] = []
            for spot in group:
                x0, y0, x1, y1 = spot.box
                steps.append(session.pixel_step(x0 + inset, y0 + inset, ms=140))
                steps.append(session.pixel_step(x1 - 1 - inset, y1 - 1 - inset, ms=140))
            ok, detail = session.run(steps, timeout=150)
            bets = (session.player_state(cancel=True).get("bets") or []) if ok else []
            if not ok:
                progress(f"  margin {inset}px: clicks failed ({detail[:100]})")
                continue
            for spot in group:
                if clean_hit(bets, spot.expect):
                    found[spot.button_id] = inset
                    todo.pop(spot.button_id, None)
        progress(
            f"  margin {inset}px: {sum(1 for v in found.values() if v == inset)} controls clean, "
            f"{len(todo)} still on a border"
        )
    return found


def refine_margins(
    session: Session,
    verdicts: Sequence[EdgeVerdict],
    spots: Sequence[Spot],
    *,
    progress: ProgressFn = lambda _l: None,
) -> list[EdgeVerdict]:
    """
    Replace the guessed corrections on failed number spots with measured ones.

    Only single-number spots are refined: an inside bet is *meant* to sit on a
    shared border, so pulling its corners off that border would be the wrong fix.
    """
    by_id = {s.button_id: s for s in spots}
    failing = [
        by_id[v.button_id]
        for v in verdicts
        if not v.ok and v.button_id in by_id and by_id[v.button_id].expect.isdigit()
    ]
    if not failing:
        return list(verdicts)

    progress(f"measuring the border band on {len(failing)} number spots ...")
    margins = measure_corner_margin(
        session, failing, window=BetWindow(session, progress=progress), progress=progress
    )
    out: list[EdgeVerdict] = []
    for verdict in verdicts:
        margin = margins.get(verdict.button_id)
        if margin is None:
            out.append(verdict)
            continue
        x0, y0, x1, y1 = verdict.box
        verdict.suggested_box = (x0 + margin, y0 + margin, x1 - margin, y1 - margin)
        verdict.note = (
            f"{verdict.note}; both corners book the number {margin} px inside the box, "
            "so the outer band belongs to the split bets that share those borders"
        ).lstrip("; ")
        out.append(verdict)
    clean = [m for m in margins.values() if m is not None]
    if clean:
        progress(
            f"  border band measured at {min(clean)}-{max(clean)} px on "
            f"{len(clean)}/{len(failing)} spots"
        )
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summarise(verdicts: Sequence[EdgeVerdict]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    bad = [v for v in verdicts if not v.ok]
    return {
        "tested": len(verdicts),
        "sound": counts.get("same", 0),
        "failed": len(bad),
        "counts": counts,
        "failures": [v.button_id for v in bad],
    }


def render_report(
    verdicts: Sequence[EdgeVerdict], *, layout_id: str, target: Target, summary: dict[str, Any]
) -> str:
    """A tester-readable table: what was clicked, what came back, what it means."""
    lines = [
        f"# Hitbox edge test - {layout_id} on {target.label}",
        "",
        f"- **When:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Method:** two clicks per control, {INSET_PX} px inside opposite corners,",
        "  compared by the event each click produced",
        f"- **Result:** {summary['sound']} sound, {summary['failed']} failed, "
        f"{summary['counts'].get('skipped', 0)} skipped of {summary['tested']}",
        "",
        "| Control | Family | Box | Verdict | Top-left / bottom-right | Note |",
        "|---------|--------|-----|---------|-------------------------|------|",
    ]
    for v in verdicts:
        size = f"{v.box[2] - v.box[0]}x{v.box[3] - v.box[1]} at {v.box[0]},{v.box[1]}"
        sigs = " / ".join(h.signature or "-" for h in v.hits) or "-"
        lines.append(
            f"| `{v.button_id}` | {v.family} | {size} | **{v.verdict}** | {sigs} | {v.note} |"
        )
    fixes = [v for v in verdicts if v.suggested_box]
    if fixes:
        lines += ["", "## Suggested corrections (not applied)", ""]
        for v in fixes:
            lines.append(f"- `{v.button_id}`: {list(v.box)} -> {list(v.suggested_box)} ({v.note})")
    return "\n".join(lines) + "\n"


def run_edge_probe(
    *,
    target: Target,
    layout_id: str = DEFAULT_LAYOUT,
    families: Sequence[str] = (),
    only: Sequence[str] = (),
    limit: int = 0,
    batch: int = BATCH,
    margin: bool = True,
    pixel: bool = False,
    progress: ProgressFn = print,
) -> dict[str, Any]:
    """Probe a skin's mapped controls by their corners and write the pack."""
    spots = collect_spots(layout_id, families=families, only=only)
    if limit > 0:
        spots = spots[:limit]
    if not spots:
        raise ValueError(f"{layout_id} has no mapped controls matching that selection")

    session = open_session(target)
    progress(f"edge probe: {len(spots)} controls on {session.describe()} ({layout_id})")
    gaps = missing_oracles(session, spots, pixel=pixel)
    if gaps:
        raise RuntimeError(
            "nothing this sweep clicked could be judged, so it did not start:\n  - "
            + "\n  - ".join(gaps)
        )
    started = time.time()
    out = out_dir_for(layout_id, target)
    out.mkdir(parents=True, exist_ok=True)
    partial = out / "edge_results.partial.json"

    def checkpoint(done: Sequence[EdgeVerdict]) -> None:
        partial.write_text(
            json.dumps(
                {"layout_id": layout_id, "target": target.label, "of": len(spots),
                 "verdicts": [v.as_dict() for v in done]},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    verdicts = probe_spots(
        session,
        spots,
        layout_id=layout_id,
        batch=batch,
        pixel=pixel,
        checkpoint=checkpoint,
        progress=progress,
    )
    if any(view_for(s) == "race" for s in spots):
        # Leave the cloth as it was found, or the next run opens on the oval.
        ensure_probe_view(session, layout_id, "square", progress=progress)
    if margin:
        verdicts = refine_margins(session, verdicts, spots, progress=progress)
    summary = summarise(verdicts)
    payload = {
        "layout_id": layout_id,
        "target": target.label,
        "target_kind": target.kind,
        "surface": session.surface,
        "inset_px": INSET_PX,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seconds": round(time.time() - started, 1),
        "summary": summary,
        "verdicts": [v.as_dict() for v in verdicts],
        "out_dir": str(out),
    }
    (out / "edge_results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out / f"EDGE-TEST-{layout_id}.md").write_text(
        render_report(verdicts, layout_id=layout_id, target=target, summary=summary),
        encoding="utf-8",
    )
    partial.unlink(missing_ok=True)
    progress(
        f"{summary['sound']}/{summary['tested']} controls sound, {summary['failed']} failed "
        f"in {payload['seconds']}s; pack in {out}"
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Prove mapped hitboxes by clicking both corners")
    p.add_argument("--target", default="10.0.0.90", help="'local' or a cabinet IP")
    p.add_argument("--layout", default=DEFAULT_LAYOUT, choices=("layout1", "layout2"))
    p.add_argument("--family", default="", help=f"comma-separated: {','.join(ALL_FAMILIES)}")
    p.add_argument("--only", default="", help="comma-separated control ids")
    p.add_argument("--limit", type=int, default=0, help="stop after N controls")
    p.add_argument(
        "--batch", type=int, default=BATCH, help="bet spots per read (1 turns batching off)"
    )
    p.add_argument(
        "--no-margin",
        action="store_true",
        help="Skip measuring the border band on failed number spots",
    )
    p.add_argument(
        "--pixel",
        action="store_true",
        help="Screenshot-diff chrome the client does not log (slow: 3 captures a control)",
    )
    p.add_argument("--list", action="store_true", help="List what would be probed and exit")
    args = p.parse_args(argv)

    families = [f for f in args.family.split(",") if f.strip()]
    only = [b for b in args.only.split(",") if b.strip()]
    if args.list:
        spots = collect_spots(args.layout, families=families, only=only)
        units = plan_batches(spots, batch=args.batch)
        for group in units:
            print(f"{group[0].family:<9} [{len(group)}] " + ", ".join(s.button_id for s in group))
        print(f"{len(spots)} controls in {len(units)} units")
        return 0

    try:
        payload = run_edge_probe(
            target=parse_target(args.target),
            layout_id=args.layout,
            families=families,
            only=only,
            limit=args.limit,
            batch=args.batch,
            margin=not args.no_margin,
            pixel=args.pixel,
        )
    except RuntimeError as exc:
        print(exc)
        return 2
    return 0 if payload["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
