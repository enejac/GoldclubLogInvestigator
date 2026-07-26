"""
Prove what every non-bet control on a roulette skin actually does.

``roulette_verify_bets`` can prove a bet spot because the middleware reports the
bet it created. Most chrome — panels, rails, chips, the eraser — leaves no such
trace, so this module proves it the other way round: click the control, take a
screenshot from inside the cabinet's interactive session, and diff it against the
frame from before the click. What changed, and where, is the evidence.

Three things make that diff trustworthy:

* The game repaints on its own, and not only in small ways: between "bets are
  open" and "no more bets" the chips grey out, the side pills dim and the banner
  changes, which is over half the screen. So a frame is never compared with one
  earlier frame but with a *set* of known-good frames — the baseline scan plus
  every settled frame since — and only the closest match counts. Pixels that
  flicker inside a single phase (countdown, last-numbers strip) are masked out on
  top of that, except inside the control being probed, which must stay visible.
* Controls that *do* touch game state (chips, the neighbour pills, the bet tools)
  are cross-checked against ``/api/data`` — chip id, bet list and credits — so
  their proof is as hard as a bet spot's.
* Anything that opens a panel is closed again and the screen is compared with the
  frame from before, so one probe cannot poison the next. If a control cannot be
  restored the sweep stops instead of clicking blind into an unknown screen.

    python -m automation.roulette_verify_ui --layout layout2
    python -m automation.roulette_verify_ui --layout layout2 --only MENU,AYUDA
    python -m automation.roulette_verify_ui --layout layout2 --group chip
    python -m automation.roulette_verify_ui --layout layout2 --write-hitboxes

The cabinet must already be showing that skin: the script clicks real pixels.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image

from automation.remote_input_agent import (
    build_input_agent_local,
    capture_client_screenshot,
    stage_input_agent,
)
from automation.roulette_layout import ROULETTE_FOCUS_PROCESS
from automation.roulette_layout_store import load_hitboxes, resolve_hitbox_center
from automation.roulette_middleware import (
    cancel_all_bets,
    fetch_player_state,
    place_bet,
    set_chip,
)
from automation.roulette_runner import (
    OPEN_UI_SETTLE_SEC,
    OPEN_UI_SETTLE_START_SEC,
    _CLOSING_RE,
    _OPEN_RE,
    _latest_file,
    wait_for_log_marker,
)
from automation.roulette_surface import DEFAULT_LAYOUT, surface_for

DEFAULT_IP = "10.0.0.90"
OUT_ROOT = Path("_tmp_logs") / "verify_ui"

# A pixel counts as changed when any channel moves this far; below it is JPEG-ish
# noise and the slow fade the cloth does between phases.
PIXEL_DELTA = 26
# Fraction of unmasked pixels that must change before a click counts as visible.
VISIBLE_RATIO = 0.0008
# Above this the screen changed as a whole (panel, view switch), not just a button.
PANEL_RATIO = 0.06
# A local change may spill this far outside the control (glow, tooltip, chip lift).
LOCAL_MARGIN_PX = 90
# Two baseline frames this close together are the same betting phase, so what
# differs between them is the game's own flicker rather than a phase change.
SAME_PHASE_RATIO = 0.03
# Enough known-good frames to hold both phases plus a few settled states.
MAX_REFERENCES = 12


def out_dir_for(layout_id: str | None = None) -> Path:
    lid = (layout_id or DEFAULT_LAYOUT).strip().lower()
    return OUT_ROOT / lid


def results_path_for(layout_id: str | None = None) -> Path:
    return out_dir_for(layout_id) / "results.json"


def hitboxes_path_for(layout_id: str | None = None) -> Path:
    lid = (layout_id or DEFAULT_LAYOUT).strip().lower()
    return Path("automation") / "layouts" / f"{lid}_hitboxes.json"


@dataclass(frozen=True, slots=True)
class Plan:
    """How one control is probed, and how the screen is put back afterwards."""

    group: str
    needs_window: bool = False
    hold_ms: int = 0
    # Bets to stand on the cloth before the click, so a bet tool has something to
    # act on. Placed through the API: what is being proven is the button, not the
    # cloth, and an API bet is exact and instant.
    pre_bets: tuple[str, ...] = ()
    # Controls to click first to reach the screen this one lives on.
    pre_open: tuple[str, ...] = ()
    # A control that toggles this one into existence. The rail pills are drawn
    # only while their rail is on, and the cloth underneath is inert, so a probe
    # run with the rail off proves nothing and quietly reports "inert".
    needs_visible: str = ""
    # Clicked immediately after the control, in the same run, when one tap is not
    # the whole gesture: BORRADOR only arms the eraser, and what it erases is the
    # bet tapped next.
    after_click: tuple[str, ...] = ()
    revert: tuple[str, ...] = ()
    # Some controls intentionally leave the screen changed (DENOM cycles the meter)
    # and have no honest revert; the sweep should not abort when they cannot be
    # put back pixel-for-pixel.
    optional_restore: bool = False
    expect: str = ""


# Probe order is deliberate: state-free controls first, controls that move money
# or open screens last, so an early surprise costs as little as possible.
GROUP_ORDER = (
    "readout", "toggle", "chip", "bet_pill", "bet_tool", "panel", "stats", "overlay",
    "flow", "unknown",
)

PLANS: dict[str, Plan] = {
    # --- readouts: mapped so automation knows to leave them alone ------------
    "LAST_BET": Plan("readout"),
    "LAST_WIN": Plan("readout"),
    "STATISTICS": Plan("readout"),
    "HISTORY_STRIP": Plan("readout"),
    "CREDIT_DISPLAY": Plan(
        "readout", needs_window=True, expect="toggles the credit readout between $ and credits"
    ),
    # Both grey out with the betting window, so both are probed inside one.
    # DENOM cycles the denomination and rescales the credit meter with it; the
    # cycle has no "back", so putting 100 COP back is a separate step.
    "DENOM": Plan(
        "toggle",
        needs_window=True,
        optional_restore=True,
        expect="cycles the credit denomination",
    ),
    "PAYTABLE": Plan("readout", needs_window=True),
    # --- the statistics panel and what lives on it ---------------------------
    # SALIR is the only way back: Escape and a second tap on the badge do not
    # close it, which is why the revert names the button.
    "PLAYER": Plan("panel", revert=("STATS_EXIT",), expect="opens the statistics panel"),
    "STATS_TITLE": Plan("stats", pre_open=("PLAYER",), revert=("STATS_EXIT",)),
    "STATS_LAST_NUMBERS": Plan("stats", pre_open=("PLAYER",), revert=("STATS_EXIT",)),
    "STATS_HOT": Plan("stats", pre_open=("PLAYER",), revert=("STATS_EXIT",)),
    "STATS_COLD": Plan("stats", pre_open=("PLAYER",), revert=("STATS_EXIT",)),
    "STATS_EXIT": Plan("stats", pre_open=("PLAYER",), expect="closes the statistics panel"),
    # --- rail toggles ---------------------------------------------------------
    # CAMBIAR VISTA toggles both ways, but only while bets are open, and the swap
    # lands seconds after the click rather than on it — miss either and an early
    # screenshot makes the control look dead.
    "CHANGE_VIEW": Plan(
        "toggle",
        needs_window=True,
        revert=("CHANGE_VIEW",),
        expect="toggles the racetrack view",
    ),
    # Neither glyph of a rail is a show or a hide: either one toggles that rail's
    # pills, so the revert for each is itself. Like the pills they command, they
    # only answer while bets are open.
    "SERIES_EYE_TOP": Plan(
        "toggle", needs_window=True, revert=("same",), expect="toggles the SERIES pills"
    ),
    "SERIES_EYE_BOTTOM": Plan(
        "toggle", needs_window=True, revert=("same",), expect="toggles the SERIES pills"
    ),
    "SERIES_EXTRA_EYE_TOP": Plan(
        "toggle", needs_window=True, revert=("same",), expect="toggles the SERIES EXTRA pills"
    ),
    "SERIES_EXTRA_EYE_BOTTOM": Plan(
        "toggle", needs_window=True, revert=("same",), expect="toggles the SERIES EXTRA pills"
    ),
    # PAÑO swaps the whole cloth for another skin, so coming back means clicking
    # the *other* skin's selector.
    "PANO": Plan(
        "panel",
        revert=("layout1:LAYOUT_SWITCH",),
        expect="swaps the cloth for the layout1 skin",
    ),
    # The language chooser has no cancel: picking the active language is the exit.
    "IDIOMA": Plan("panel", revert=("LANG_ES",), expect="opens the LANGUAGE chooser"),
    "LANG_TITLE": Plan("overlay", pre_open=("IDIOMA",), revert=("LANG_ES",)),
    "LANG_EN": Plan("overlay", pre_open=("IDIOMA",), revert=("IDIOMA", "LANG_ES")),
    "LANG_ES": Plan("overlay", pre_open=("IDIOMA",), expect="closes the chooser in Spanish"),
    # The help book: SALIR is the only way out, so every probe on it reverts there.
    "AYUDA": Plan("panel", revert=("HELP_EXIT",), expect="opens the help book"),
    "HELP_TITLE": Plan("overlay", pre_open=("AYUDA",), revert=("HELP_EXIT",)),
    "HELP_INTRO": Plan("overlay", pre_open=("AYUDA",), revert=("HELP_EXIT",)),
    "HELP_LEGEND": Plan("overlay", pre_open=("AYUDA",), revert=("HELP_EXIT",)),
    "HELP_BACK": Plan("overlay", pre_open=("AYUDA",), revert=("HELP_EXIT",)),
    "HELP_NEXT": Plan("overlay", pre_open=("AYUDA",), revert=("HELP_EXIT",)),
    "HELP_EXIT": Plan("overlay", pre_open=("AYUDA",), expect="closes the help book"),
    # The MENÚ overlay dims the game; a second tap on MENÚ puts it away. Its own
    # IDIOMA and AYUDA slabs lead to the screens those top-bar icons open, so each
    # is reverted through that screen's exit and then the overlay's.
    "MENU": Plan("panel", revert=("same",), expect="opens the MENÚ overlay"),
    "MENU_IDIOMA": Plan("overlay", pre_open=("MENU",), revert=("LANG_ES", "MENU")),
    "MENU_AYUDA": Plan("overlay", pre_open=("MENU",), revert=("HELP_EXIT", "MENU")),
    # --- chips: proved against chip_id in the player state --------------------
    # The tray greys out between windows and ignores taps then, which is how
    # chip_5 and chip_10 first came back "inert" while chip_50 and chip_100
    # happened to be probed inside a window and passed.
    "chip_1": Plan("chip", needs_window=True),
    "chip_5": Plan("chip", needs_window=True),
    "chip_10": Plan("chip", needs_window=True),
    "chip_50": Plan("chip", needs_window=True),
    "chip_100": Plan("chip", needs_window=True),
    # --- neighbour / call-bet pills: proved against the reported bets ---------
    # Each pill lives on a rail that can be toggled off, and the cloth left behind
    # is inert, so the probe turns its rail on first if it has to.
    # The SERIES EXTRA three are relative bets: the help book defines them against
    # the last straight bet, so each is probed with one standing.
    "VECINOS": Plan(
        "bet_pill", needs_window=True, needs_visible="SERIES_EXTRA_EYE_TOP", pre_bets=("17",)
    ),
    "FINALES": Plan(
        "bet_pill", needs_window=True, needs_visible="SERIES_EXTRA_EYE_TOP", pre_bets=("17",)
    ),
    "COMPLETO": Plan(
        "bet_pill", needs_window=True, needs_visible="SERIES_EXTRA_EYE_TOP", pre_bets=("17",)
    ),
    "VECINOS_0": Plan("bet_pill", needs_window=True, needs_visible="SERIES_EYE_TOP"),
    "HUERFANOS": Plan("bet_pill", needs_window=True, needs_visible="SERIES_EYE_TOP"),
    "VECINOS_00": Plan("bet_pill", needs_window=True, needs_visible="SERIES_EYE_TOP"),
    # --- tools that act on bets already on the cloth --------------------------
    "CANCELAR_TODO": Plan("bet_tool", needs_window=True, pre_bets=("17", "5")),
    # The eraser is two taps: it arms, then the bet tapped next is the one that
    # goes. One tap on its own changes nothing, which is what made it look inert.
    "BORRADOR": Plan(
        "bet_tool",
        needs_window=True,
        pre_bets=("17", "5"),
        after_click=("17",),
        expect="arms the eraser; the next tap removes that bet",
    ),
    "REPETIR": Plan("bet_tool", needs_window=True, pre_bets=("17",)),
    # Not a screen: the help book calls it "show/hide the maximum win for each bet
    # placed", so it needs a bet standing to have anything to draw.
    "MUESTRA_GANANCIAS": Plan(
        "toggle",
        needs_window=True,
        pre_bets=("17",),
        revert=("same",),
        expect="shows/hides the maximum win per standing bet",
    ),
    # --- game flow ------------------------------------------------------------
    "START": Plan("flow", needs_window=True, expect="closes betting and spins"),
}

# Never clicked by this sweep, whatever the registry says. EDGE_TAB is not a game
# control at all: it is the TeamViewer sidebar handle bleeding through at the right
# screen edge, and clicking it puts a remote-support panel over the cabinet.
# PIN LOCK would lock the cabinet behind a PIN nobody here knows, and LLAMAR AL
# ASISTENTE raises a service call on a cabinet other people share, which no click
# of ours can take back. The help book states what both do, which is proof enough
# to map them without pressing them.
NEVER_CLICK = ("COBRAR", "EDGE_TAB", "MENU_PIN_LOCK", "MENU_ASSISTANT")

# Mapped chip id -> the 0-based index the middleware reports in ``chip_id``.
CHIP_INDEX = {"chip_1": 0, "chip_5": 1, "chip_10": 2, "chip_50": 3, "chip_100": 4}

# Readouts that change on their own schedule — once per spin, not once per frame,
# so a short baseline scan cannot discover them. They are masked for every probe
# except their own.
VOLATILE_IDS = ("HISTORY_STRIP", "LAST_BET", "LAST_WIN", "CREDIT_DISPLAY", "START", "PLAYER")


@dataclass
class Probe:
    """One control's evidence, on its way to becoming a registry entry."""

    button_id: str
    plan: Plan
    record: dict[str, Any] = field(default_factory=dict)


def ui_targets(
    layout_id: str | None = None,
    *,
    ids: Iterable[str] | None = None,
    groups: Iterable[str] | None = None,
) -> list[str]:
    """Mapped non-bet controls, in probe order."""
    buttons = load_hitboxes(layout_id).get("buttons") or {}
    mapped = [
        bid
        for bid, entry in buttons.items()
        if entry.get("kind") in ("ui", "readout", "slider", "stats_panel", "overlay")
        and entry.get("click_area_pct")
    ]
    if ids:
        wanted = [i.strip() for i in ids if i.strip()]
        missing = [i for i in wanted if i not in mapped]
        if missing:
            raise KeyError(f"not mapped UI controls: {missing}")
        return wanted
    keep = [b for b in mapped if b not in NEVER_CLICK]
    # The slider has its own calibrated tool; clicking its centre would only move
    # opacity to 50%.
    keep = [b for b in keep if b not in ("OPACITY_SLIDER",)]
    if groups is not None:
        want = {g.strip().lower() for g in groups if g.strip()}
        keep = [b for b in keep if plan_for(b).group in want]
    return sorted(keep, key=lambda b: (GROUP_ORDER.index(plan_for(b).group), b))


def plan_for(button_id: str) -> Plan:
    return PLANS.get(button_id, Plan("unknown", revert=("escape", "same")))


# ---------------------------------------------------------------------------
# Image diffing
# ---------------------------------------------------------------------------


def _load(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)


def diff_mask(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    return (np.abs(after - before).max(axis=2) >= PIXEL_DELTA)


def _dilate(mask: np.ndarray, radius: int = 3) -> np.ndarray:
    """Grow a mask by a few pixels without pulling in scipy."""
    out = mask.copy()
    for shift in range(1, radius + 1):
        out[shift:, :] |= mask[:-shift, :]
        out[:-shift, :] |= mask[shift:, :]
        out[:, shift:] |= mask[:, :-shift]
        out[:, :-shift] |= mask[:, shift:]
    return out


def _describe(before: np.ndarray, after: np.ndarray, ignore: np.ndarray) -> dict[str, Any]:
    changed = diff_mask(before, after) & ~ignore
    total = int((~ignore).sum()) or 1
    count = int(changed.sum())
    out: dict[str, Any] = {
        "changed_px": count,
        "changed_ratio": round(count / total, 6),
        "bbox": None,
    }
    if count:
        ys, xs = np.nonzero(changed)
        out["bbox"] = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    return out


def describe_change(
    frame: np.ndarray, references: list[np.ndarray], ignore: np.ndarray
) -> dict[str, Any]:
    """
    How far a frame is from the nearest state the game is known to rest in.

    Taking the closest reference is what makes the verdict survive the betting
    cycle: a frame shot while bets are closed matches the closed-phase reference
    and the phase itself contributes nothing to the score.
    """
    best = {"changed_px": 0, "changed_ratio": 1.0, "bbox": None}
    for ref in references:
        got = _describe(ref, frame, ignore)
        if got["changed_ratio"] < best["changed_ratio"]:
            best = got
    return best


def _bbox_inside(bbox: list[int] | None, box: tuple[int, int, int, int], margin: int) -> bool:
    if bbox is None:
        return False
    x0, y0, x1, y1 = box
    return (
        bbox[0] >= x0 - margin
        and bbox[1] >= y0 - margin
        and bbox[2] <= x1 + margin
        and bbox[3] <= y1 + margin
    )


def idle_churn(references: list[np.ndarray], ignore: np.ndarray) -> float:
    """
    How much the visible area moves when nobody touches it.

    Needed because a readout's own box is deliberately left unmasked — the digits
    are the whole point of probing it — which lets its natural churn back in. The
    last-numbers strip gains a number every spin and the DENOMINACIÓN panel dims
    when bets close, and either would otherwise be reported as the click's doing.
    Measured against the same reference set the probe is judged by, so it is the
    floor that a real effect has to clear.
    """
    worst = 0.0
    for i, ref in enumerate(references):
        others = references[:i] + references[i + 1 :]
        if others:
            worst = max(worst, describe_change(ref, others, ignore)["changed_ratio"])
    return worst


def classify(change: dict[str, Any], box: tuple[int, int, int, int], floor: float = 0.0) -> str:
    ratio = change["changed_ratio"]
    if ratio < max(VISIBLE_RATIO, floor):
        return "no_visible_change"
    if ratio >= PANEL_RATIO:
        return "screen_change"
    if _bbox_inside(change["bbox"], box, LOCAL_MARGIN_PX):
        return "local_change"
    return "wide_change"


# ---------------------------------------------------------------------------
# Cabinet interaction
# ---------------------------------------------------------------------------


def _click_step(x_pct: float, y_pct: float, hold_ms: int = 0) -> dict[str, Any]:
    return {
        "type": "click_window",
        "value": f"{ROULETTE_FOCUS_PROCESS}@{x_pct:.3f},{y_pct:.3f}",
        "ms": max(90, hold_ms),
    }


def _steps_for(button_id: str, layout_id: str, hold_ms: int = 0) -> list[dict[str, Any]]:
    center = resolve_hitbox_center(button_id, layout_id)
    if center is None:
        raise KeyError(f"{button_id} has no resolvable centre on {layout_id}")
    return [_click_step(center.x_pct, center.y_pct, hold_ms)]


def _revert_steps(action: str, button_id: str, layout_id: str) -> list[dict[str, Any]]:
    """
    Steps that undo a probe: Escape, the control itself, or another control.

    ``"layout1:LAYOUT_SWITCH"`` names a control on a *different* skin, which is
    what it takes to come back from PAÑO: that button swaps the cloth for another
    skin, whose own selector sits somewhere else entirely.
    """
    if action == "escape":
        return [{"type": "key", "value": "ESCAPE"}, {"type": "sleep", "ms": 1200}]
    target = button_id if action == "same" else action
    where = layout_id
    if ":" in target:
        where, target = target.split(":", 1)
    return _steps_for(target, where) + [{"type": "sleep", "ms": 1200}]


def capture(ip: str, agent: Any, dest: Path, *, steps: list[dict[str, Any]] | None = None,
            settle_ms: int = 1600) -> Path:
    """
    Screenshot the game, optionally after running some input steps.

    The settle is generous because the view switch animates for about a second:
    at 700 ms the racetrack was caught mid-transition and the click looked inert.
    """
    ok, detail = capture_client_screenshot(
        ip=ip,
        agent=agent,
        dest=dest,
        focus_process=ROULETTE_FOCUS_PROCESS,
        settle_ms=settle_ms,
        steps=steps,
    )
    if not ok:
        raise RuntimeError(f"capture failed: {detail}")
    return dest


def baseline(ip: str, agent: Any, work: Path, *, frames: int = 5,
             progress: Any = print) -> tuple[list[np.ndarray], np.ndarray]:
    """
    Collect the states the untouched game rests in, plus its self-flicker mask.

    A capture takes long enough that consecutive frames already straddle the
    betting cycle, so no artificial delay is needed to see both phases. Frames
    that are close to each other are the same phase; what differs *within* such a
    pair is flicker the game owns, and that is what gets masked.
    """
    shots: list[np.ndarray] = []
    for i in range(max(2, frames)):
        shots.append(_load(capture(ip, agent, work / f"baseline_{i}.png")))
        progress(f"  baseline frame {i + 1}/{frames}")

    mask = np.zeros(shots[0].shape[:2], dtype=bool)
    pairs = 0
    for i in range(len(shots)):
        for j in range(i + 1, len(shots)):
            same_phase = _describe(shots[i], shots[j], mask & False)
            if same_phase["changed_ratio"] <= SAME_PHASE_RATIO:
                mask |= diff_mask(shots[i], shots[j])
                pairs += 1
    mask = _dilate(mask, 3)
    progress(
        f"  {len(shots)} reference frames, {pairs} same-phase pairs, "
        f"{mask.mean() * 100:.2f}% of pixels flicker on their own"
    )
    return shots, mask


def phase_banner_box(surface: Any) -> tuple[int, int, int, int] | None:
    """
    The band between the last-numbers strip and the cloth, where the game writes
    "REALICE SUS APUESTAS" / "NO MÁS APUESTAS".

    It has no button of its own, so it is derived from the two things it sits
    between rather than hard-coded per skin.
    """
    strip = surface.ui.get("HISTORY_STRIP")
    if not strip or not surface.numbers:
        return None
    cloth_top = min(box[1] for box in surface.numbers.values())
    if cloth_top <= strip[3]:
        return None
    return (0, strip[3], surface.client_w, cloth_top)


def slow_volatile_mask(surface: Any, shape: tuple[int, ...]) -> np.ndarray:
    """Readout boxes plus the phase banner, as a mask."""
    mask = np.zeros(shape[:2], dtype=bool)
    boxes = [surface.ui[name] for name in VOLATILE_IDS if name in surface.ui]
    banner = phase_banner_box(surface)
    if banner:
        boxes.append(banner)
    for box in boxes:
        mask |= _box_mask(shape, box)
    return mask


def home_reference(layout_id: str) -> Path | None:
    """The last overlay capture of the base screen, used as the "is it home?" proof."""
    return view_reference(layout_id, "square")


def view_reference(layout_id: str, view: str) -> Path | None:
    """Stored capture of one of the two cloths a skin can show."""
    lid = (layout_id or DEFAULT_LAYOUT).strip().lower()
    base = Path("_tmp_logs") / "map_overlay"
    name = "screen_base.png" if view == "square" else f"screen_{view}.png"
    shot = (base if lid == DEFAULT_LAYOUT else base / lid) / name
    return shot if shot.is_file() else None


def cloth_box(surface: Any) -> tuple[int, int, int, int]:
    """Bounding box of the number grid — the skin's fingerprint."""
    boxes = list(surface.numbers.values())
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


# The number grid keeps its shape in every phase, but not quite its pixels: it
# dims while bets are shut and the winning cell is lit for a while afterwards,
# which measured up to 8%. Anything that is actually a different screen is an
# order of magnitude past that — the racetrack scores ~87% and the statistics
# panel covers the grid outright — so the line sits well clear of the flicker.
HOME_RATIO = 0.20


def _off_home(frame: np.ndarray, ref: np.ndarray, box: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = box
    zero = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    return _describe(ref[y0:y1, x0:x1], frame[y0:y1, x0:x1], zero)["changed_ratio"]


class BaseScreen:
    """
    "Is the game back on its own cloth?", asked of the number grid alone.

    The full frame cannot answer it. Between phases the banner swaps, the chips
    and pills grey out and the dozens light up under the winning number, which is
    a tenth of the screen without anything being wrong. The grid itself is drawn
    identically in every phase, so it is the part worth comparing — and it is
    covered outright by any panel, which is the case that matters.
    """

    def __init__(self, layout_id: str) -> None:
        path = home_reference(layout_id)
        self.box = cloth_box(surface_for(layout_id))
        self.ref = _load(path) if path else None

    def off(self, frame: np.ndarray) -> float | None:
        if self.ref is None or self.ref.shape != frame.shape:
            return None
        return _off_home(frame, self.ref, self.box)

    def at_home(self, frame: np.ndarray) -> bool | None:
        off = self.off(frame)
        return None if off is None else off < HOME_RATIO


def ensure_home(ip: str, agent: Any, layout_id: str, work: Path, *, progress: Any = print) -> bool:
    """
    Make sure the sweep starts on the skin's base screen.

    A panel left open by an earlier run would otherwise be baselined as "normal",
    and every probe after it would click into the wrong screen and report nothing.
    Only the number grid is compared, because everything else on the screen
    legitimately differs between betting phases.
    """
    base = BaseScreen(layout_id)
    if base.ref is None:
        progress("  no base-screen reference; assuming the cabinet is home")
        return True
    surface = surface_for(layout_id)
    frame = _load(capture(ip, agent, work / "home_check.png"))
    view = current_view(frame, layout_id)
    off = base.off(frame) or 0.0
    if off < HOME_RATIO:
        return True

    # layout2 chrome is in the same place on both cloths. If a run lands on the
    # racetrack and CAMBIAR VISTA will not answer, probing here beats aborting.
    if view == "race" and layout_id == "layout2":
        progress("  on the racetrack view; chrome probes may continue here")
        for action in ("escape", "STATS_EXIT", "LANG_ES", "HELP_EXIT", "MENU"):
            frame = _load(
                capture(
                    ip, agent, work / "home_check.png",
                    steps=_revert_steps(action, action, layout_id),
                )
            )
            if current_view(frame, layout_id) == "race":
                return True
        return True

    progress(f"  not on the base screen ({off * 100:.1f}% off the cloth); trying to get back")
    # The racetrack is not a panel and has no exit button: it is the other view,
    # and CAMBIAR VISTA only answers inside a betting window, so it gets the
    # waiting treatment rather than a blind click.
    if current_view(frame, layout_id) == "race":
        progress("  the game is on the racetrack view; switching back")
        if ensure_view(ip, agent, layout_id, work, view="square", progress=progress):
            return True

    # Every known way back, cheapest first: a modal's own exit, the racetrack
    # toggle, then the other skin's selector in case PAÑO swapped the cloth.
    exits = ["escape"]
    if "STATS_EXIT" in surface.stats:
        exits.append("STATS_EXIT")
    if "LANG_ES" in (surface.overlays.get("language") or {}):
        exits.append("LANG_ES")
    if "HELP_EXIT" in (surface.overlays.get("help") or {}):
        exits.append("HELP_EXIT")
    if "menu" in surface.overlays and "MENU" in surface.ui:
        exits.append("MENU")
    if layout_id != "layout1":
        exits.append("layout1:LAYOUT_SWITCH")
    if "PANO" in surface.ui:
        exits.append("PANO")
    for action in exits:
        frame = _load(
            capture(ip, agent, work / "home_check.png", steps=_revert_steps(action, action, layout_id))
        )
        off = base.off(frame) or 0.0
        progress(f"  {action}: {off * 100:.1f}% off the cloth")
        if off < HOME_RATIO:
            return True
    return False


def _roulette_log(ip: str) -> Path:
    log = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta Roulette"))
    if log is None:
        raise FileNotFoundError(f"ruleta Roulette log missing on {ip}")
    return log


def current_view(frame: np.ndarray, layout_id: str) -> str:
    """Which cloth the frame shows: ``square``, ``race`` or ``unknown``."""
    box = cloth_box(surface_for(layout_id))
    scores: dict[str, float] = {}
    for view in ("square", "race"):
        path = view_reference(layout_id, view)
        ref = _load(path) if path else None
        if ref is not None and ref.shape == frame.shape:
            scores[view] = _off_home(frame, ref, box)
    if not scores:
        return "unknown"
    best = min(scores, key=scores.__getitem__)
    return best if scores[best] < HOME_RATIO else "unknown"


def ensure_view(
    ip: str,
    agent: Any,
    layout_id: str,
    work: Path,
    *,
    view: str = "square",
    attempts: int = 3,
    progress: Any = print,
) -> bool:
    """
    Put the game on the square cloth or the racetrack, and prove it with a capture.

    CAMBIAR VISTA only answers while the betting window is open, and the swap
    animates for about a second after the click, so each attempt waits for the
    window and lets the capture's own settle cover the animation.
    """
    work.mkdir(parents=True, exist_ok=True)
    seen = current_view(_load(capture(ip, agent, work / "view_check.png")), layout_id)
    if seen == view:
        return True
    log = _roulette_log(ip)
    offset = log.stat().st_size
    for attempt in range(1, attempts + 1):
        progress(f"  view is {seen}, want {view}; pressing CAMBIAR VISTA ({attempt}/{attempts})")
        offset, matched, detail = wait_for_log_marker(
            log, start_offset=offset, pattern=_OPEN_RE, timeout_sec=120.0
        )
        if not matched:
            progress(f"  no betting window to switch in: {detail}")
            break
        time.sleep(OPEN_UI_SETTLE_SEC)
        frame = _load(
            capture(
                ip,
                agent,
                work / "view_check.png",
                steps=_steps_for("CHANGE_VIEW", layout_id),
                settle_ms=2200,
            )
        )
        seen = current_view(frame, layout_id)
        if seen == view:
            return True
    if view == "square" and layout_id != "layout1":
        progress("  CAMBIAR VISTA did not land; trying PANO -> layout1 -> back")
        for steps in (
            _steps_for("PANO", layout_id),
            _revert_steps("layout1:LAYOUT_SWITCH", "PANO", layout_id),
        ):
            frame = _load(capture(ip, agent, work / "view_check.png", steps=steps, settle_ms=2200))
            seen = current_view(frame, layout_id)
            if seen == view:
                return True
    return False


def _bet_summary(bets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"type": str(b.get("BetType") or ""), "id": str(b.get("Id") or ""),
         "credits": int(b.get("CreditValue") or 0)}
        for b in bets
    ]


def probe_one(
    ip: str,
    button_id: str,
    *,
    agent: Any,
    layout_id: str,
    base: BaseScreen,
    references: list[np.ndarray],
    volatile: np.ndarray,
    work: Path,
    log: Path,
    offset: int,
    progress: Any = print,
) -> tuple[dict[str, Any], int]:
    """
    Click one control and describe what it did.

    ``references`` is the growing set of states the game is known to rest in; a
    settled frame is added to it so the next probe is not blamed for a change this
    one legitimately left behind (a different chip, say).
    """
    plan = plan_for(button_id)
    surface = surface_for(layout_id)
    box = surface.ui.get(button_id) or surface.all_areas_px[button_id]
    record: dict[str, Any] = {
        "id": button_id,
        "group": plan.group,
        "expect": plan.expect,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    # The control's own box stays unmasked: for a readout like CRÉDITO the whole
    # point is the digits, which is exactly what the flicker mask would hide.
    ignore = volatile & ~_box_mask(volatile.shape, box)

    if plan.group in ("bet_pill", "bet_tool", "chip", "flow"):
        cancel_all_bets(ip)
        set_chip(ip, 0)

    if plan.needs_visible:
        toggled, offset = _make_visible(
            ip, button_id, agent=agent, layout_id=layout_id, box=box,
            toggle=plan.needs_visible, work=work, log=log, offset=offset, progress=progress,
        )
        record["rail_toggled"] = toggled
    # A frame of the screen this control actually sits on. When there is one it is
    # the *only* thing worth diffing against: for a control on a panel, "back to
    # the base cloth" is a change, and the reference set would call it a match.
    pre_frame: np.ndarray | None = None
    if plan.pre_open:
        steps: list[dict[str, Any]] = []
        for opener in plan.pre_open:
            steps += _steps_for(opener, layout_id) + [{"type": "sleep", "ms": 500}]
        record["pre_open"] = list(plan.pre_open)
        pre_frame = _load(capture(ip, agent, work / f"{button_id}_pre.png", steps=steps))

    # The window is waited for *here*, right before the click, and not a step
    # earlier: a capture costs 12-19 s and the window is only open for 22, so a
    # wait done before the preparation shots leaves the click landing in "no más
    # apuestas", where half the chrome is grey and ignores it.
    if plan.needs_window:
        offset, matched, detail = wait_for_log_marker(
            log, start_offset=offset, pattern=_OPEN_RE, timeout_sec=120
        )
        record["window"] = detail if matched else f"no window: {detail}"
        if not matched:
            record["ok"] = False
            record["error"] = "no betting window"
            return record, offset
        # Tray / START stay grey briefly after the log marker.
        settle = OPEN_UI_SETTLE_START_SEC if button_id == "START" else OPEN_UI_SETTLE_SEC
        time.sleep(settle)

    if plan.pre_bets:
        placed = place_bet(ip, list(plan.pre_bets))
        record["pre_bets"] = {"bets": list(plan.pre_bets), "accepted": bool(placed.get("success"))}
        time.sleep(0.4)

    state_before = fetch_player_state(ip)

    after_path = work / f"{button_id}_after.png"
    steps = _steps_for(button_id, layout_id, plan.hold_ms)
    for follow_up in plan.after_click:
        steps += [{"type": "sleep", "ms": 400}] + _steps_for(follow_up, layout_id)
    if plan.after_click:
        record["after_click"] = list(plan.after_click)
    capture(ip, agent, after_path, steps=steps)
    after = _load(after_path)
    state_after = fetch_player_state(ip)

    probe_refs = [pre_frame] if pre_frame is not None else references
    # 1.5x, so a control has to move visibly more than its own idle churn rather
    # than merely match it on a lucky frame.
    floor = max(VISIBLE_RATIO, 1.5 * idle_churn(probe_refs, ignore))
    if floor > VISIBLE_RATIO:
        progress(f"  this area drifts on its own; needs more than {floor * 100:.2f}% to count")
    change = describe_change(after, probe_refs, ignore)
    # The cloth/view switches do not repaint on the click: they wait for the game
    # to reach a safe moment, which is several seconds later. A control that looks
    # inert gets a second look before it is written off.
    if change["changed_ratio"] < floor:
        later_path = work / f"{button_id}_later.png"
        later = _load(capture(ip, agent, later_path))
        later_change = describe_change(later, probe_refs, ignore)
        if later_change["changed_ratio"] >= floor:
            progress("  effect arrived late, after the click settled")
            change, after, after_path = later_change, later, later_path
            record["deferred"] = True

    record.update(change)
    record["idle_floor"] = round(floor, 6)
    record["verdict"] = classify(change, box, floor)
    record["click_box_px"] = list(box)
    record["shots"] = {"after": str(after_path)}
    record["state"] = {
        "chip_before": state_before.get("chip_id"),
        "chip_after": state_after.get("chip_id"),
        "credits_before": state_before.get("credits"),
        "credits_after": state_after.get("credits"),
        "bets_before": _bet_summary(state_before.get("bets") or []),
        "bets_after": _bet_summary(state_after.get("bets") or []),
    }

    if plan.group == "chip":
        want = CHIP_INDEX.get(button_id)
        got = state_after.get("chip_id")
        record["ok"] = got is not None and int(got) == want
        record["effect"] = f"selects chip index {got} (expected {want})"
    elif plan.group in ("bet_pill", "bet_tool"):
        after_bets = _bet_summary(state_after.get("bets") or [])
        before_bets = _bet_summary(state_before.get("bets") or [])
        record["ok"] = after_bets != before_bets or record["verdict"] != "no_visible_change"
        record["effect"] = (
            f"bets {len(before_bets)} -> {len(after_bets)}: "
            f"{[b['type'] + ' ' + b['id'] for b in after_bets][:6]}"
        )
    elif plan.group == "flow":
        # START ends the betting window early, and the banner it changes is masked
        # as volatile, so the log is the only honest witness here.
        offset, closed, detail = wait_for_log_marker(
            log, start_offset=offset, pattern=_CLOSING_RE, timeout_sec=8
        )
        record["ok"] = closed
        record["effect"] = f"betting closed after the click: {detail}" if closed else (
            "no closing marker within 8 s"
        )
    else:
        record["ok"] = record["verdict"] != "no_visible_change"
        record["effect"] = record["verdict"]

    # Leave no stake behind, whatever the control did.
    if plan.pre_bets or plan.group in ("bet_pill", "bet_tool"):
        cancel_all_bets(ip)

    # A control whose own click leads home (SALIR) needs no revert; check first,
    # otherwise the fallback revert would click blind into the base screen.
    if _settled(after, base, references, volatile):
        record["restored"] = True
        _remember(references, after)
        return record, offset

    needs_restore = record["verdict"] == "screen_change" or plan.pre_open or (
        plan.revert and record["verdict"] != "no_visible_change"
    )
    if needs_restore:
        restored, path, frame = _restore(
            ip, button_id, agent=agent, layout_id=layout_id, plan=plan, work=work,
            base=base, references=references, volatile=volatile, log=log, offset=offset,
            progress=progress,
        )
        record["restored"] = restored
        record["shots"]["restored"] = str(path)
        if restored:
            _remember(references, frame)
    else:
        record["restored"] = True
        _remember(references, after)

    return record, offset


# A drawn control has an outline and lettering; bare cloth is a flat wash. Edge
# density says which one is there, and unlike brightness it does not move when the
# pills dim for "no more bets".
DRAWN_EDGES = 0.02


def is_drawn(frame: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    x0, y0, x1, y1 = box
    crop = frame[y0:y1, x0:x1].mean(axis=2)
    if crop.size == 0:
        return False
    return float((np.abs(np.diff(crop, axis=1)) > 18).mean()) > DRAWN_EDGES


def _box_mask(shape: tuple[int, ...], box: tuple[int, int, int, int]) -> np.ndarray:
    mask = np.zeros(shape[:2], dtype=bool)
    x0, y0, x1, y1 = box
    mask[max(0, y0) : y1, max(0, x0) : x1] = True
    return mask


def _make_visible(
    ip: str,
    button_id: str,
    *,
    agent: Any,
    layout_id: str,
    box: tuple[int, int, int, int],
    toggle: str,
    work: Path,
    log: Path,
    offset: int,
    progress: Any = print,
) -> tuple[bool, int]:
    """
    Turn the control's rail on if the control is not drawn.

    The rail glyphs are as window-gated as everything else on this cloth, so the
    toggle waits for an open window too. Returns whether it clicked, and the log
    offset the caller should carry on from.
    """
    frame = _load(capture(ip, agent, work / f"{button_id}_rail.png", settle_ms=300))
    if is_drawn(frame, box):
        return False, offset
    progress(f"  {button_id} is not drawn; toggling {toggle} first")
    offset, matched, _ = wait_for_log_marker(
        log, start_offset=offset, pattern=_OPEN_RE, timeout_sec=120
    )
    if not matched:
        return False, offset
    time.sleep(OPEN_UI_SETTLE_SEC)
    capture(ip, agent, work / f"{button_id}_rail.png", steps=_steps_for(toggle, layout_id))
    return True, offset


def _remember(references: list[np.ndarray], frame: np.ndarray) -> None:
    """Keep the most recent settled states; the baseline frames stay at the front."""
    references.append(frame)
    if len(references) > MAX_REFERENCES:
        del references[len(references) - MAX_REFERENCES]


def _settled(
    frame: np.ndarray, base: BaseScreen, references: list[np.ndarray], volatile: np.ndarray
) -> bool:
    """Whether the game is back on its own cloth, phase and rails notwithstanding."""
    at_home = base.at_home(frame)
    if at_home is not None:
        return at_home
    return describe_change(frame, references, volatile)["changed_ratio"] < PANEL_RATIO


def _restore(
    ip: str,
    button_id: str,
    *,
    agent: Any,
    layout_id: str,
    plan: Plan,
    work: Path,
    base: BaseScreen,
    references: list[np.ndarray],
    volatile: np.ndarray,
    log: Path | None = None,
    offset: int = 0,
    progress: Any = print,
) -> tuple[bool, Path, np.ndarray]:
    """Put the screen back to a known-good state; report whether it worked."""
    actions = plan.revert or ("escape", "same")
    path = work / f"{button_id}_restored.png"
    frame = np.zeros((1, 1, 3), dtype=np.int16)
    for action in actions:
        # A control that only answers inside a betting window does not answer to
        # its own revert outside one either: that is how one CAMBIAR VISTA probe
        # left the whole sweep stranded on the racetrack.
        if plan.needs_window and log is not None:
            offset, _, _ = wait_for_log_marker(
                log, start_offset=offset, pattern=_OPEN_RE, timeout_sec=120
            )
            time.sleep(OPEN_UI_SETTLE_SEC)
        steps: list[dict[str, Any]] | None = _revert_steps(action, button_id, layout_id)
        # Two looks per action, for the same reason a probe gets two: a view swap
        # lands seconds after the click that asked for it.
        for attempt in range(2):
            capture(ip, agent, path, steps=steps)
            steps = None
            frame = _load(path)
            if _settled(frame, base, references, volatile):
                progress(f"  restored with {action}")
                return True, path, frame
            off = base.off(frame)
            left = off if off is not None else describe_change(
                frame, references, volatile
            )["changed_ratio"]
            progress(
                f"  {action} left {left * 100:.1f}% changed"
                f"{' (looking again)' if attempt == 0 else ''}"
            )
    # A view toggle can miss if pressed outside the betting window; the cloth
    # grid is the proof that matters, not escape.
    if current_view(frame, layout_id) == "race":
        progress("  still on the racetrack; trying ensure_view(square)")
        if ensure_view(ip, agent, layout_id, work, view="square", progress=progress):
            frame = _load(capture(ip, agent, path))
            if _settled(frame, base, references, volatile):
                return True, path, frame
    return False, path, frame


def verify_ui(
    ip: str = DEFAULT_IP,
    *,
    layout_id: str | None = None,
    ids: Iterable[str] | None = None,
    groups: Iterable[str] | None = None,
    progress: Any = print,
) -> dict[str, Any]:
    layout_id = (layout_id or DEFAULT_LAYOUT).strip().lower()
    targets = ui_targets(layout_id, ids=ids, groups=groups)
    work = out_dir_for(layout_id) / "shots"
    work.mkdir(parents=True, exist_ok=True)

    agent = stage_input_agent(
        ip=ip, local_exe=build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
    )
    log = _roulette_log(ip)
    offset = log.stat().st_size

    progress(f"start: {layout_id}, {len(targets)} controls")
    if not ensure_home(ip, agent, layout_id, work, progress=progress):
        raise RuntimeError(
            "cabinet is not on the base screen and could not be returned to it; "
            f"see {work / 'home_check.png'}"
        )
    base = BaseScreen(layout_id)
    references, volatile = baseline(ip, agent, work, progress=progress)
    volatile |= slow_volatile_mask(surface_for(layout_id), volatile.shape)
    progress(f"  masked with the slow readouts: {volatile.mean() * 100:.2f}% of the screen")

    results = _load_previous(results_path_for(layout_id), exclude=targets)
    aborted = ""
    for button_id in targets:
        plan = plan_for(button_id)
        progress(f"probe {button_id} ({plan.group})")
        try:
            record, offset = probe_one(
                ip, button_id, agent=agent, layout_id=layout_id, base=base,
                references=references, volatile=volatile, work=work, log=log,
                offset=offset, progress=progress,
            )
        except Exception as exc:  # a broken probe must not strand the cabinet
            record = {"id": button_id, "group": plan.group, "ok": False, "error": str(exc)}
            progress(f"  ERROR {exc}")
        results[button_id] = record
        mark = "OK " if record.get("ok") else "-- "
        progress(
            f"  {mark}{record.get('verdict', record.get('error', ''))}"
            f" {record.get('effect', '')}"
        )
        if record.get("restored") is False and not plan.optional_restore:
            aborted = f"{button_id} left the screen changed; stopping so it can be inspected"
            progress(f"  ABORT {aborted}")
            break
        if record.get("restored") is False and plan.optional_restore:
            progress(f"  {button_id} left readout state changed (expected); continuing")
            record["restored"] = True
            shot = (record.get("shots") or {}).get("restored") or (record.get("shots") or {}).get("after")
            if shot:
                _remember(references, _load(Path(shot)))

    summary = {
        "ip": ip,
        "layout_id": layout_id,
        "tested": len([r for r in results.values() if r.get("id") in targets]),
        "with_effect": len([r for r in results.values() if r.get("ok")]),
        "inert": [k for k, r in results.items() if r.get("ok") is False and not r.get("error")],
        "errors": [k for k, r in results.items() if r.get("error")],
        "aborted": aborted,
        "results": results,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    path = results_path_for(layout_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    progress(f"wrote {path}")
    return summary


def _load_previous(path: Path, *, exclude: Iterable[str]) -> dict[str, Any]:
    """Keep verdicts for controls this run does not touch."""
    if not path.is_file():
        return {}
    try:
        old = json.loads(path.read_text(encoding="utf-8-sig")).get("results") or {}
    except (OSError, json.JSONDecodeError):
        return {}
    skip = set(exclude)
    return {k: v for k, v in old.items() if k not in skip}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

VERDICT_NOTE = {
    "local_change": "click lands on the control and only it repaints",
    "wide_change": "click repaints an area beyond the control",
    "screen_change": "click opens/replaces a screen",
    "no_visible_change": "click has no visible or reported effect",
}


def apply_results_to_hitboxes(
    *, layout_id: str | None = None, results_path: Path | None = None,
    hitboxes_path: Path | None = None,
) -> dict[str, Any]:
    """Fold the sweep's verdicts into the skin's registry as proof blocks."""
    layout_id = (layout_id or DEFAULT_LAYOUT).strip().lower()
    results_path = results_path or results_path_for(layout_id)
    hitboxes_path = hitboxes_path or hitboxes_path_for(layout_id)
    results = json.loads(results_path.read_text(encoding="utf-8-sig")).get("results") or {}
    data = json.loads(hitboxes_path.read_text(encoding="utf-8-sig"))
    buttons = data.get("buttons") or {}

    updated: list[str] = []
    for button_id, res in results.items():
        entry = buttons.get(button_id)
        if not isinstance(entry, dict) or res.get("error"):
            continue
        entry["verified"] = {
            "method": "ui_probe",
            "verdict": res.get("verdict"),
            "effect": res.get("effect"),
            "changed_ratio": res.get("changed_ratio"),
            "bbox": res.get("bbox"),
            "chip_after": (res.get("state") or {}).get("chip_after"),
            "bets_after": (res.get("state") or {}).get("bets_after"),
            "checked_at": res.get("checked_at") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        if res.get("verdict") == "no_visible_change" and not res.get("ok"):
            entry["automation"] = entry.get("automation") or "inert"
        updated.append(button_id)

    status = data.setdefault("status", {})
    status["ui_controls_probed"] = len(updated)
    status["ui_controls_inert"] = len(
        [b for b in updated if (buttons[b].get("verified") or {}).get("verdict") == "no_visible_change"]
    )
    status["ui_checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    hitboxes_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"updated": len(updated), "ids": sorted(updated)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Prove what each non-bet roulette control does")
    p.add_argument("--ip", default=DEFAULT_IP)
    p.add_argument("--layout", default=DEFAULT_LAYOUT, choices=("layout1", "layout2"))
    p.add_argument("--only", default=None, help="Comma-separated control ids")
    p.add_argument("--group", default=None, help=f"Comma-separated groups: {'|'.join(GROUP_ORDER)}")
    p.add_argument("--write-hitboxes", action="store_true", help="Fold results into the registry")
    p.add_argument("--list", action="store_true", help="Print the probe order and exit")
    args = p.parse_args(argv)

    if args.write_hitboxes:
        print(json.dumps(apply_results_to_hitboxes(layout_id=args.layout), indent=2))
        return 0

    ids = args.only.split(",") if args.only else None
    groups = args.group.split(",") if args.group else None
    if args.list:
        for bid in ui_targets(args.layout, ids=ids, groups=groups):
            print(f"{plan_for(bid).group:<9} {bid}")
        return 0

    summary = verify_ui(args.ip, layout_id=args.layout, ids=ids, groups=groups)
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "results"},
            indent=2,
        )
    )
    return 0 if not summary["aborted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
