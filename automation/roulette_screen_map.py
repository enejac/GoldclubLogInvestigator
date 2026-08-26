"""
Re-map the roulette screen the cabinet is showing right now.

The older board mapper assumed one screen — layout1's betting cloth — and walked a
fixed anchor list. That breaks the moment the software team ships a new help book,
a second history panel or nudges a row of buttons ten pixels left: the boxes in
the registry still exist, they just no longer sit on the controls.

This module maps *whatever is open*, in three steps:

1. **Identify.** A capture is scored against every screen the skin knows. Screens
   with a stored baseline are matched on pixels; screens without one are matched on
   evidence instead — their mapped boxes are compared with the base cloth, because
   an open panel covers the cloth it is drawn over. Nothing matches when the screen
   is genuinely new, and that is reported rather than guessed.
2. **Relocate.** Each mapped box of the chosen screen is cut out of the baseline
   and searched for around its old position in the new capture, coarse then fine.
   A control that moved comes back with its new box; one that cannot be found is
   reported ``lost`` instead of being silently overwritten.
3. **Verify.** Cloth screens are proven through the middleware (a real chip on a
   real cell, ``PlayerDataBets`` as truth); overlay screens are proven with the
   pixel-diff probes in :mod:`automation.roulette_verify_ui`. Same run, different
   oracle — a panel button leaves no middleware trace, and a bet cell deserves
   better proof than a repaint.

Only step 1 needs the operator to hold the screen open. Verification navigates on
its own afterwards.

    python -m automation.roulette_screen_map --ip 10.0.0.90 --layout layout2 --detect
    python -m automation.roulette_screen_map --ip 10.0.0.90 --layout layout2 --screen help
    python -m automation.roulette_screen_map --ip 10.0.0.90 --layout layout2 \
        --new-screen "history v2" --apply
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from PIL import Image

from app_paths import app_tmp_logs_dir
from automation.roulette_layout_store import (
    hitboxes_path as registry_path,
    invalidate_hitbox_cache,
    load_hitboxes,
)
from automation.roulette_surface import (
    DEFAULT_LAYOUT,
    KNOWN_LAYOUTS,
    Surface,
    area_pct_dict,
    surface_for,
)
from automation.roulette_target import Session, Target, cabinet_target, open_session

Box = tuple[int, int, int, int]
ProgressFn = Callable[[str], None]

# Cloth views are betting surfaces, not panels: they are proven through the
# middleware and they already own the baseline names the overlay tools write.
CLOTH_BASELINES = {"base": "screen_base.png", "racetrack": "screen_race.png"}
CLOTH_SCREENS = tuple(CLOTH_BASELINES)

# A pixel counts as changed when a channel moves this far — same threshold the UI
# verifier uses, so a "moved" verdict here means the same thing it does there.
PIXEL_DELTA = 26
# How far a capture may sit from a screen's baseline and still be that screen.
# Distance is 1 - correlation of gradient magnitudes (see _identity_distance), and
# the two limits come from measurements on .90:
#
#   cloth, whole frame     0.00-0.11 across a betting cycle, 0.34 with the menu
#                          rail open during "no más apuestas", 0.96 for the other
#                          skin or another screen
#   panel, own footprint    0.00 while it is up, 0.84 once it is closed
#
# Each line sits in its own empty middle. A cloth screen is judged loosely because
# a rail or a small modal leaves the cloth itself perfectly recognisable; a panel is
# judged inside its own boxes, where being open or closed is night and day.
SAME_CLOTH_MAX = 0.45
SAME_PANEL_MAX = 0.30
# Below this the cloth is not merely recognisable but bare: nothing is drawn over it.
# Measured on .90: 0.01-0.04 with nothing open, 0.15-0.21 behind the language chooser,
# 0.33 behind the menu rail during "no más apuestas".
CLOTH_BARE_MAX = 0.10
# Padding around a panel's boxes when its footprint is cut out of the frame.
FOOTPRINT_PAD_PX = 12
# Two consecutive captures this close together mean nothing is animating any more.
# The betting countdown and the history strip keep ticking, so it is not zero.
SETTLED_MAX = 0.08
# A screen with no baseline is matched on how thoroughly its boxes cover the base
# cloth instead. An open panel repaints nearly all of its own area.
EVIDENCE_MATCH_MIN = 0.45
# Two candidate screens this close together are not a confident answer.
AMBIGUOUS_MARGIN = 0.05

# How far a control is allowed to have drifted and still be found.
SEARCH_RADIUS_PX = 140
# The coarse pass runs on every Nth pixel of both images, which is what keeps a
# 140 px search affordable: a cloth cell takes ~0.5 s instead of ~7 s.
COARSE_SCALE = 4
# Fallback step for crops too small to survive the downscale.
COARSE_STEP_PX = 4
# Coarse hits carried into the full-resolution pass, so a look-alike cell cannot
# win the search on blur alone.
COARSE_CANDIDATES = 8
# Coarse hits that earn a single-pixel pass after the two-pixel one.
FINE_CANDIDATES = 3
# How much of a ratio a candidate at the far edge of the search has to win by
# before it beats one sitting where the control already was.
DISTANCE_PRIOR = 0.05
# Below this the crop was not really found again and the control is called lost.
TEMPLATE_MIN_SCORE = 0.55
# Moving a control rewrites the registry, so it takes more than a plausible match.
# A control that is really there matches at 100% on a live capture; the look-alikes
# the search settles for when a rail is drawn over the control it wants scored 71-85%
# on .90 and proposed five bets that had not moved at all.
MOVE_MIN_SCORE = 0.92
# Movement under this is measurement noise, not a layout change.
MOVE_MIN_PX = 3
# A crop with less of its own area standing out than this carries no evidence of
# where it is: it is blank, or a control that is currently not drawn.
FEATURELESS_RATIO = 0.02

# Discovery of controls the registry does not know yet, on the downscaled mask.
DISCOVERY_SCALE = 4
DISCOVERY_MIN_PX = 40 * 40
DISCOVERY_MAX_BOXES = 40
# An edge this strong counts as something drawn rather than a gradient in the cloth.
EDGE_DELTA = 40
# Fragments this close together (in pooled cells) belong to one control: a caption
# is several words, and each word is its own patch of new edges.
MERGE_X = 3
MERGE_Y = 1

# --- skin fingerprint -------------------------------------------------------
# Ratio of edge energy on a skin's expected cell borders to the average across the
# grid. Measured on .90: the skin that is really on screen scores 2.3-2.4, the
# other one 0.6, because its borders fall inside the wrong cells.
SKIN_FIT_MIN = 1.3
# ...and it has to win by this much before it is called a mismatch. With a full-screen
# panel up there is no cloth to measure and the numbers are noise — the help book fit
# layout1 at 3.24 and layout2 at 1.94 on a layout2 cabinet — whereas a cabinet really
# on the other skin separates by 3.6x.
SKIN_FIT_MARGIN = 2.0

_NAME_RE = re.compile(r"[^a-z0-9]+")
_RESERVED_KEYS = frozenset({"base", "square", "cloth", "all", "ui", "board", "none", "new"})


# ---------------------------------------------------------------------------
# Screen catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenSpec:
    """One screen of a skin, and how much of it is already mapped."""

    layout_id: str
    key: str
    name: str
    kind: str  # "cloth" | "overlay"
    control_ids: tuple[str, ...] = ()
    mapped_ids: tuple[str, ...] = ()
    verified_ids: tuple[str, ...] = ()
    opened_by: str = ""
    closed_by: str = ""
    source: str = "surface"  # "surface" | "registry"

    @property
    def baseline(self) -> Path | None:
        path = baseline_path(self.layout_id, self.key)
        return path if path.is_file() else None

    @property
    def state(self) -> str:
        if not self.control_ids:
            return "unmapped"
        if not self.mapped_ids:
            return "unmapped"
        if len(self.mapped_ids) < len(self.control_ids):
            return "partial"
        return "mapped"

    @property
    def label(self) -> str:
        bits = [f"{len(self.mapped_ids)}/{len(self.control_ids)} controls", self.state]
        if self.baseline is None:
            bits.append("no baseline")
        return f"{self.name} ({', '.join(bits)})"


def as_target(where: Target | str) -> Target:
    """Accept a Target or a bare cabinet IP, so older call sites keep working."""
    if isinstance(where, Target):
        return where
    return cabinet_target(str(where))


def out_root() -> Path:
    """Run workspace, beside the exe when frozen so captures survive the process."""
    return app_tmp_logs_dir() / "screen_map"


def baseline_dir(layout_id: str) -> Path:
    """Where the overlay renderer keeps this skin's captures."""
    lid = _lid(layout_id)
    base = app_tmp_logs_dir() / "map_overlay"
    return base if lid == DEFAULT_LAYOUT else base / lid


def baseline_path(layout_id: str, screen_key: str) -> Path:
    name = CLOTH_BASELINES.get(screen_key) or f"screen_{screen_key}.png"
    return baseline_dir(layout_id) / name


def out_dir_for(layout_id: str, screen_key: str) -> Path:
    return out_root() / _lid(layout_id) / screen_key


def _lid(layout_id: str | None) -> str:
    return (layout_id or DEFAULT_LAYOUT).strip().lower()


def _registry_screens(layout_id: str) -> dict[str, dict[str, Any]]:
    """Screen metadata this skin's registry carries for screens not in code."""
    data = load_hitboxes(layout_id)
    screens = data.get("screens")
    return screens if isinstance(screens, dict) else {}


def _mapped_and_verified(
    buttons: dict[str, Any], ids: Iterable[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    mapped: list[str] = []
    verified: list[str] = []
    for bid in ids:
        entry = buttons.get(bid)
        if not isinstance(entry, dict) or not entry.get("click_area_pct"):
            continue
        mapped.append(bid)
        if entry.get("verified"):
            verified.append(bid)
    return tuple(mapped), tuple(verified)


def _pretty(key: str) -> str:
    return key.replace("_", " ").strip().title()


def known_screens(layout_id: str | None = None) -> list[ScreenSpec]:
    """
    Every screen the skin can show, code-defined and registry-defined alike.

    The cloth views come first because they are where betting happens, then the
    sub-screens in the order the surface declares them, then anything only the
    registry knows — that is where a screen mapped by this tool ends up.
    """
    lid = _lid(layout_id)
    surface = surface_for(lid)
    buttons = load_hitboxes(lid).get("buttons") or {}
    meta = _registry_screens(lid)
    specs: list[ScreenSpec] = []

    cloth_ids = tuple(surface.numbers) + tuple(surface.outside)
    for key in CLOTH_SCREENS:
        if key == "racetrack" and not any(
            bid.startswith(("race_", "mini_")) for bid in buttons
        ):
            continue
        ids = (
            tuple(bid for bid in buttons if bid.startswith(("race_", "mini_")))
            if key == "racetrack"
            else cloth_ids
        )
        mapped, verified = _mapped_and_verified(buttons, ids)
        specs.append(
            ScreenSpec(
                layout_id=lid,
                key=key,
                name="Betting cloth" if key == "base" else "Racetrack view",
                kind="cloth",
                control_ids=ids,
                mapped_ids=mapped,
                verified_ids=verified,
                opened_by="" if key == "base" else "CHANGE_VIEW",
                closed_by="" if key == "base" else "CHANGE_VIEW",
            )
        )

    seen = {spec.key for spec in specs}
    for key, boxes in surface.overlays.items():
        # The racetrack is a betting view, not a panel; it is already listed above
        # with the cloth screens and must not appear twice.
        if key in seen:
            continue
        ids = tuple(boxes)
        mapped, verified = _mapped_and_verified(buttons, ids)
        info = meta.get(key) or {}
        specs.append(
            ScreenSpec(
                layout_id=lid,
                key=key,
                name=str(info.get("name") or _pretty(key)),
                kind="overlay",
                control_ids=ids,
                mapped_ids=mapped,
                verified_ids=verified,
                opened_by=str(info.get("opened_by") or ""),
                closed_by=str(info.get("closed_by") or ""),
            )
        )
        seen.add(key)

    extra: dict[str, list[str]] = {}
    for bid, entry in buttons.items():
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("overlay") or "")
        if key and key not in seen:
            extra.setdefault(key, []).append(bid)
    for key, ids in sorted(extra.items()):
        mapped, verified = _mapped_and_verified(buttons, ids)
        info = meta.get(key) or {}
        specs.append(
            ScreenSpec(
                layout_id=lid,
                key=key,
                name=str(info.get("name") or _pretty(key)),
                kind="overlay",
                control_ids=tuple(sorted(ids)),
                mapped_ids=mapped,
                verified_ids=verified,
                opened_by=str(info.get("opened_by") or ""),
                closed_by=str(info.get("closed_by") or ""),
                source="registry",
            )
        )
        seen.add(key)

    # A screen someone has just named has a baseline and an entry of its own but no
    # controls yet — its candidates still need confirming. It has to be listed anyway,
    # or the next run would offer to create it again.
    for key, info in sorted(meta.items()):
        if key in seen or not isinstance(info, dict):
            continue
        specs.append(
            ScreenSpec(
                layout_id=lid,
                key=key,
                name=str(info.get("name") or _pretty(key)),
                kind=str(info.get("kind") or "overlay"),
                control_ids=(),
                mapped_ids=(),
                verified_ids=(),
                opened_by=str(info.get("opened_by") or ""),
                closed_by=str(info.get("closed_by") or ""),
                source="registry",
            )
        )
    return specs


def screen_by_key(layout_id: str | None, screen_key: str) -> ScreenSpec | None:
    for spec in known_screens(layout_id):
        if spec.key == screen_key:
            return spec
    return None


def slugify_screen_key(name: str) -> str:
    """Turn an operator's screen name into a registry key."""
    slug = _NAME_RE.sub("_", (name or "").strip().lower()).strip("_")
    return slug


def validate_new_screen_name(layout_id: str | None, name: str) -> str:
    """Return the key a new screen would get, or raise with the reason it cannot."""
    slug = slugify_screen_key(name)
    if len(slug) < 2:
        raise ValueError("Give the screen a name of at least two letters or digits.")
    if len(slug) > 32:
        raise ValueError("Screen name is too long (32 characters after cleanup).")
    if slug[0].isdigit():
        raise ValueError("Screen name has to start with a letter.")
    if slug in _RESERVED_KEYS:
        raise ValueError(f"{slug!r} is reserved; pick another name.")
    if screen_by_key(layout_id, slug) is not None:
        raise ValueError(
            f"{_lid(layout_id)} already has a screen called {slug!r}; "
            "re-map that one instead of naming a new screen."
        )
    return slug


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def load_frame(path: Path | str) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)


def _changed_ratio(before: np.ndarray, after: np.ndarray) -> float:
    """Fraction of pixels that moved, on colour frames or on a single channel."""
    if before.shape != after.shape or before.size == 0:
        return 1.0
    delta = np.abs(after - before)
    if delta.ndim == 3:
        delta = delta.max(axis=2)
    return float((delta >= PIXEL_DELTA).mean())


def _detail(template: np.ndarray) -> float:
    """How much of a crop stands out from its own background."""
    if template.size == 0:
        return 0.0
    flat = template if template.ndim == 2 else template.max(axis=2)
    return float((np.abs(flat - int(np.median(flat))) >= PIXEL_DELTA).mean())


def _luma(frame: np.ndarray) -> np.ndarray:
    """
    One channel to search in, three times cheaper than the colour frame.

    Sliding a template over a 140 px radius is thousands of comparisons, and the
    per-channel maximum is most of their cost. Brightness is enough to find a
    control again — the cloth's reds, blacks and greens are far apart in it — while
    the verdict score is still read off this same measure, so it stays comparable
    between runs.
    """
    return (frame.sum(axis=2) // 3).astype(np.int16)


def _pool(plane: np.ndarray, k: int) -> np.ndarray:
    """
    Shrink by averaging k x k blocks, not by throwing pixels away.

    Striding every fourth pixel is what a first version did, and it made the coarse
    pass blind to any drift that was not a multiple of four: on a screen full of
    one-pixel grid lines and thin glyphs, sampling the shifted copy hits different
    pixels entirely and the true offset scores worse than a wrong one. Averaging
    keeps a shifted control looking like itself. Both planes are pooled on the same
    grid, anchored at the image origin, so pooled offsets stay comparable.
    """
    h = plane.shape[0] // k * k
    w = plane.shape[1] // k * k
    if h < k or w < k:
        return plane
    return plane[:h, :w].reshape(h // k, k, w // k, k).mean(axis=(1, 3)).astype(np.int16)


def _crop(frame: np.ndarray, box: Box) -> np.ndarray:
    x0, y0, x1, y1 = box
    return frame[max(0, y0) : y1, max(0, x0) : x1]


def _scaled_box(box: Box, src: tuple[int, int], dst: tuple[int, int]) -> Box:
    """Rescale a box measured at *src* (w, h) onto a frame of *dst* (w, h)."""
    if src == dst:
        return box
    sx, sy = dst[0] / src[0], dst[1] / src[1]
    x0, y0, x1, y1 = box
    return (
        int(round(x0 * sx)),
        int(round(y0 * sy)),
        max(int(round(x0 * sx)) + 1, int(round(x1 * sx))),
        max(int(round(y0 * sy)) + 1, int(round(y1 * sy))),
    )


def _frame_size(frame: np.ndarray) -> tuple[int, int]:
    return frame.shape[1], frame.shape[0]


# ---------------------------------------------------------------------------
# Identification
# ---------------------------------------------------------------------------


def _gradient(plane: np.ndarray) -> np.ndarray:
    """Edge energy per pixel: how much brighter its neighbours are."""
    out = np.zeros(plane.shape, dtype=np.int32)
    wide = plane.astype(np.int32)
    out[:, :-1] += np.abs(np.diff(wide, axis=1))
    out[:-1, :] += np.abs(np.diff(wide, axis=0))
    return out


def skin_fit(frame: np.ndarray, layout_id: str) -> float:
    """
    How well a skin's number grid lines up with what is on screen.

    The two skins draw the same 12-column cloth in different places, so a capture
    of the wrong one has its cell borders inside our cells rather than on them.
    Comparing edge energy on the expected borders with the average over the grid
    separates them cleanly — 2.3 for the skin that is really up, 0.6 for the other
    — and needs no stored baseline, which is what makes it usable as a guard
    *before* any baseline exists.
    """
    surface = surface_for(layout_id)
    boxes = list(surface.numbers.values())
    if not boxes:
        return 0.0
    size = _frame_size(frame)
    boxes = [_scaled_box(b, (surface.client_w, surface.client_h), size) for b in boxes]
    x0, x1 = min(b[0] for b in boxes), max(b[2] for b in boxes)
    y0, y1 = min(b[1] for b in boxes), max(b[3] for b in boxes)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return 0.0
    grid = np.abs(np.diff(_luma(frame).astype(np.int32), axis=1))[y0:y1, x0 : x1 - 1]
    borders = sorted({b[0] for b in boxes} | {b[2] for b in boxes})
    cols = [x - x0 - 1 for x in borders if 0 < x - x0 - 1 < grid.shape[1]]
    if not cols:
        return 0.0
    return float(grid[:, cols].mean() / max(1.0, grid.mean()))


def identify_skin(frame: np.ndarray) -> list[tuple[str, float]]:
    """Every known skin with its fit against *frame*, best first."""
    from automation.roulette_surface import KNOWN_LAYOUTS

    fits = [(lid, skin_fit(frame, lid)) for lid in KNOWN_LAYOUTS]
    fits.sort(key=lambda f: -f[1])
    return fits


def skin_mismatch(frame: np.ndarray, layout_id: str) -> str:
    """
    Why *layout_id* is the wrong skin for this capture, or "" when there is no case.

    Only positive evidence counts: the other skin's grid has to be found, not merely
    ours missed. A full-screen panel covers the cloth entirely — the statistics screen
    scored 1.08 and 1.13, below the bar for both skins — and refusing to map a panel
    because the cloth behind it cannot be measured would block the very screens this
    tool exists for.
    """
    fits = dict(identify_skin(frame))
    mine = fits.get(_lid(layout_id), 0.0)
    if mine >= SKIN_FIT_MIN:
        return ""
    better = [
        (lid, f)
        for lid, f in fits.items()
        if f >= SKIN_FIT_MIN and lid != _lid(layout_id) and f >= SKIN_FIT_MARGIN * mine
    ]
    if not better:
        return ""
    lid, fit = max(better, key=lambda f: f[1])
    return (
        f"the cabinet is showing {lid} (grid fit {fit:.2f}), not "
        f"{_lid(layout_id)} (fit {mine:.2f})"
    )


def skin_unreadable(frame: np.ndarray, layout_id: str) -> bool:
    """True when no skin's grid can be found, so the fingerprint has nothing to say."""
    return all(fit < SKIN_FIT_MIN for _lid_, fit in identify_skin(frame))


@dataclass(frozen=True)
class ScreenMatch:
    """How well one known screen explains the capture in hand."""

    key: str
    name: str
    kind: str
    state: str
    score: float  # lower is a better match
    basis: str  # "baseline" | "evidence"
    matched: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "kind": self.kind,
            "state": self.state,
            "score": round(self.score, 4),
            "basis": self.basis,
            "matched": self.matched,
        }


def _pool_mean(plane: np.ndarray, k: int = DISCOVERY_SCALE) -> np.ndarray:
    h = plane.shape[0] // k * k
    w = plane.shape[1] // k * k
    return plane[:h, :w].astype(np.float64).reshape(h // k, k, w // k, k).mean(axis=(1, 3))


def _identity_distance(a: np.ndarray, b: np.ndarray) -> float:
    """
    How different two captures are, ignoring brightness: 0.0 is the same picture.

    Counting changed pixels cannot answer "is this the same screen" on this game,
    because opening any panel dims everything behind it: the base cloth measured 0.64
    changed against its own baseline during "no más apuestas" with a rail open, worse
    than a capture of an entirely different skin. What survives dimming is where the
    edges are, so the two frames are compared as correlated gradient fields, which is
    blind to a global change in contrast and still collapses when the picture changes.
    """
    ga, gb = _pool_mean(_gradient(_luma(a))), _pool_mean(_gradient(_luma(b)))
    if ga.shape != gb.shape or ga.size == 0:
        return 1.0
    ga = ga - ga.mean()
    gb = gb - gb.mean()
    denom = float(np.sqrt((ga * ga).sum() * (gb * gb).sum()))
    if denom <= 0.0:
        return 1.0
    return max(0.0, 1.0 - float((ga * gb).sum()) / denom)


def _footprint(boxes: Sequence[Box], size: tuple[int, int]) -> Box | None:
    """The area a screen's own controls cover, padded, or None when it has none."""
    if not boxes:
        return None
    w, h = size
    x0 = max(0, min(b[0] for b in boxes) - FOOTPRINT_PAD_PX)
    y0 = max(0, min(b[1] for b in boxes) - FOOTPRINT_PAD_PX)
    x1 = min(w, max(b[2] for b in boxes) + FOOTPRINT_PAD_PX)
    y1 = min(h, max(b[3] for b in boxes) + FOOTPRINT_PAD_PX)
    return (x0, y0, x1, y1) if x1 - x0 >= 8 and y1 - y0 >= 8 else None


def _match_brightness(ref: np.ndarray, frame: np.ndarray) -> np.ndarray:
    """
    *ref* rescaled to *frame*'s brightness and contrast.

    Relocation compares raw pixels, so the dim that "no más apuestas" lays over the
    game would otherwise read as every control having changed. One global gain and
    offset, fitted on the two luminance fields, puts them back on the same scale.
    """
    a = _luma(ref).astype(np.float64)
    b = _luma(frame).astype(np.float64)
    sa, sb = a.std(), b.std()
    if sa < 1e-6:
        return ref
    gain = float(np.clip(sb / sa, 0.25, 4.0))
    offset = float(b.mean() - gain * a.mean())
    return np.clip(ref.astype(np.float64) * gain + offset, 0, 255).astype(ref.dtype)


def _evidence_score(frame: np.ndarray, base_ref: np.ndarray | None, boxes: list[Box]) -> float:
    """
    How much of a screen's own area is repainted relative to the base cloth.

    Used for screens with no stored baseline: a panel that is up covers what the
    cloth drew there, so its boxes differ almost everywhere. Returned as a
    match score (lower is better) so it can be ranked next to pixel matches.
    """
    if base_ref is None or not boxes:
        return 1.0
    ratios = []
    for box in boxes:
        before, after = _crop(base_ref, box), _crop(frame, box)
        if before.size == 0 or before.shape != after.shape:
            continue
        ratios.append(_changed_ratio(before, after))
    if not ratios:
        return 1.0
    return max(0.0, 1.0 - float(np.mean(ratios)))


def identify_screen(
    frame: np.ndarray, layout_id: str | None = None, *, specs: Sequence[ScreenSpec] | None = None
) -> list[ScreenMatch]:
    """Rank the skin's screens by how well each explains *frame*, best first."""
    lid = _lid(layout_id)
    surface = surface_for(lid)
    specs = list(specs if specs is not None else known_screens(lid))
    size = _frame_size(frame)
    surface_size = (surface.client_w, surface.client_h)

    base_path = baseline_path(lid, "base")
    base_ref = load_frame(base_path) if base_path.is_file() else None
    if base_ref is not None and _frame_size(base_ref) != size:
        base_ref = None

    matches: list[ScreenMatch] = []
    for spec in specs:
        ref_path = spec.baseline
        ref = load_frame(ref_path) if ref_path else None
        boxes = [
            _scaled_box(box, surface_size, size)
            for box in _boxes_for(spec, surface, lid).values()
        ]
        if ref is not None and _frame_size(ref) == size:
            # A panel is judged inside its own boxes: the whole frame is mostly the
            # same cloth whether a small modal is up or not, so full-frame scoring
            # cannot see it, while its own footprint changes completely.
            region = _footprint(boxes, size) if spec.kind == "overlay" else None
            if region is None:
                score = _identity_distance(ref, frame)
                limit = SAME_CLOTH_MAX
            else:
                score = _identity_distance(_crop(ref, region), _crop(frame, region))
                limit = SAME_PANEL_MAX
            basis, matched = "baseline", score <= limit
        else:
            score = _evidence_score(frame, base_ref, boxes)
            basis = "evidence"
            matched = score <= (1.0 - EVIDENCE_MATCH_MIN) and spec.kind == "overlay"
        matches.append(
            ScreenMatch(
                key=spec.key,
                name=spec.name,
                kind=spec.kind,
                state=spec.state,
                score=score,
                basis=basis,
                matched=matched,
            )
        )
    matches.sort(key=lambda m: (not m.matched, m.score))
    return matches


def map_refusal(matches: Sequence[ScreenMatch], screen_key: str, *, is_new: bool) -> str:
    """
    Why mapping *screen_key* now would be a mistake, or "" when it is fine to go on.

    Which screen is open is asked of each screen separately rather than settled by a
    ranking, because more than one can be up at once: the menu rail is drawn *over* the
    base cloth, and both are genuinely there. So the only question is whether the screen
    the operator named is the one in front of us, and there are three ways to be sure it
    is not — it has a baseline and does not match it, it has none while another screen
    does match its own, or the operator is naming a screen the skin already knows.
    """
    proven = [m for m in matches if m.matched and m.basis == "baseline"]
    mine = next((m for m in matches if m.key == screen_key), None)
    others = [m for m in proven if m.key != screen_key]
    # A cloth match only rules out other screens while the cloth is *bare*. The language
    # chooser covers a fourteenth of the frame, so the cloth still matched at 0.17 with
    # it wide open, against 0.01-0.04 with nothing over it.
    blocking = [m for m in others if m.kind == "overlay" or m.score <= CLOTH_BARE_MAX]
    if is_new:
        if not blocking:
            return ""
        hit = blocking[0]
        return (
            f"This looks like the already-mapped {hit.name!r} screen "
            f"({hit.key}, matched its baseline at {hit.score:.3f}). "
            "Re-map that screen instead of naming a new one."
        )
    if mine is not None and mine.basis == "baseline" and not mine.matched:
        instead = f"; it matches {others[0].name!r} ({others[0].key})" if others else ""
        return (
            f"The open screen does not look like {screen_key!r} "
            f"(baseline distance {mine.score:.3f}){instead}. "
            "Open the screen you meant to map, or pick the matching one."
        )
    if blocking and (mine is None or mine.basis != "baseline"):
        hit = blocking[0]
        return (
            f"The open screen matches {hit.name!r} ({hit.key}), not {screen_key!r}, "
            "which has no baseline to check against. "
            "Open the screen you meant to map, or pick the matching one."
        )
    return ""


def suggest_screen(matches: Sequence[ScreenMatch]) -> tuple[ScreenMatch | None, bool]:
    """
    Best match plus whether it is proof or merely a guess.

    Only a baseline match counts as proof. Evidence is a weak signal by
    construction: every panel is drawn over the same cloth, so a full-screen one
    like the help book blanks the boxes of *all* of them and the ranking between
    them is close to arbitrary — on .90 the open language chooser scored the menu's
    boxes best. It is worth offering as a starting guess, never worth acting on, so
    a caller that refuses to proceed on a mismatch will not refuse on this.
    """
    hits = [m for m in matches if m.matched]
    if not hits:
        return (None, False)
    best = hits[0]
    if best.basis != "baseline":
        return (best, False)
    others = [m for m in hits[1:] if m.basis == "baseline"]
    if not others:
        return (best, True)
    return (best, (others[0].score - best.score) >= AMBIGUOUS_MARGIN)


# ---------------------------------------------------------------------------
# Relocation
# ---------------------------------------------------------------------------


@dataclass
class Relocation:
    """Where one known control ended up in the new capture."""

    button_id: str
    old_box: Box
    new_box: Box | None
    dx: int
    dy: int
    score: float
    status: str  # "unchanged" | "moved" | "lost" | "featureless" | "covered"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.button_id,
            "old_box": list(self.old_box),
            "new_box": list(self.new_box) if self.new_box else None,
            "dx": self.dx,
            "dy": self.dy,
            "score": round(self.score, 4),
            "status": self.status,
        }


def _boxes_for(spec: ScreenSpec, surface: Surface, layout_id: str) -> dict[str, Box]:
    """Pixel boxes for a screen's controls, registry first then surface geometry."""
    buttons = load_hitboxes(layout_id).get("buttons") or {}
    out: dict[str, Box] = {}
    for bid in spec.control_ids:
        entry = buttons.get(bid)
        if isinstance(entry, dict) and all(
            isinstance(entry.get(k), (int, float)) for k in ("x", "y", "width", "height")
        ):
            x, y = int(entry["x"]), int(entry["y"])
            out[bid] = (x, y, x + int(entry["width"]), y + int(entry["height"]))
            continue
        box = surface.all_areas_px.get(bid)
        if box:
            out[bid] = box
    return out


def _offset_candidates(
    template: np.ndarray,
    frame: np.ndarray,
    origin: tuple[int, int],
    *,
    radius: int,
    step: int,
    top: int = 1,
    prior: float = 0.0,
    prior_from: tuple[int, int] = (0, 0),
    prior_radius: int = 0,
) -> list[tuple[int, int, float]]:
    """
    Offsets within +/-radius of *origin*, best (least changed) first.

    More than one is kept because the cloth is full of look-alikes: at quarter
    scale a red 5 and a red 23 are nearly the same blur, so the single best coarse
    hit is not reliably the right cell. The caller re-scores the shortlist at full
    resolution, where they are told apart.

    *prior* nudges the ranking towards offsets near the control's old position,
    measured on the total displacement (*prior_from* is how far this pass's origin
    already sits from the original box). It matters at every pass: the cloth has
    eighteen red cells, so a shortlist of eight can otherwise be filled with twins
    and never include the true spot, and a blank patch matches every offset equally
    well. Scores are returned unpenalised; only the order is affected.
    """
    th, tw = template.shape[:2]
    fh, fw = frame.shape[:2]
    ox, oy = origin
    found: list[tuple[int, int, float]] = []
    for dy in range(-radius, radius + 1, step):
        y = oy + dy
        if y < 0 or y + th > fh:
            continue
        for dx in range(-radius, radius + 1, step):
            x = ox + dx
            if x < 0 or x + tw > fw:
                continue
            found.append((dx, dy, _changed_ratio(template, frame[y : y + th, x : x + tw])))
    if not found:
        return [(0, 0, 1.0)]
    weight = prior / (2 * max(1, prior_radius or radius)) if prior else 0.0
    fx, fy = prior_from
    found.sort(key=lambda c: c[2] + weight * (abs(c[0] + fx) + abs(c[1] + fy)))
    return found[: max(1, top)]


def relocate_control(
    baseline: np.ndarray,
    frame: np.ndarray,
    box: Box,
    button_id: str = "",
    *,
    radius: int = SEARCH_RADIUS_PX,
) -> Relocation:
    """
    Find one control's box again in a fresh capture.

    Three passes, each narrower and more expensive than the last: a quarter-scale
    sweep of the whole radius, a two-pixel pass around each of its best hits, then
    single pixels around the two that survived. The shortlist is what makes it
    right on a cloth of look-alike cells, and the pyramid is what makes it quick.
    The crop is taken from the baseline, so what is being matched is the control as
    it was actually drawn, not a guess at it.
    """
    return _relocate(_search_space(baseline, frame), box, button_id, radius=radius)


def _search_space(baseline: np.ndarray, frame: np.ndarray) -> tuple[np.ndarray, ...]:
    """Brightness planes plus their pooled copies, shared by every control."""
    base_l, frame_l = _luma(baseline), _luma(frame)
    return base_l, frame_l, _pool(base_l, COARSE_SCALE), _pool(frame_l, COARSE_SCALE)


def _relocate(
    space: tuple[np.ndarray, ...],
    box: Box,
    button_id: str = "",
    *,
    radius: int = SEARCH_RADIUS_PX,
) -> Relocation:
    baseline, frame, base_pooled, frame_pooled = space
    template = _crop(baseline, box)
    if template.size == 0:
        return Relocation(button_id, box, None, 0, 0, 0.0, "lost")
    # A patch with nothing drawn in it — the SERIES pills while their rail is off,
    # a slab of flat cloth — matches every offset perfectly, so searching for it
    # says nothing about where it is. Better to report no evidence than to move it.
    if _detail(template) < FEATURELESS_RATIO:
        return Relocation(button_id, box, box, 0, 0, 0.0, "featureless")

    scale = COARSE_SCALE if min(template.shape[:2]) >= 4 * COARSE_SCALE else 1
    if scale > 1:
        pooled_box = tuple(v // scale for v in box)
        shortlist = [
            (dx * scale, dy * scale)
            for dx, dy, _ in _offset_candidates(
                _crop(base_pooled, pooled_box),
                frame_pooled,
                (pooled_box[0], pooled_box[1]),
                radius=max(1, radius // scale),
                step=1,
                top=COARSE_CANDIDATES,
                prior=DISTANCE_PRIOR,
            )
        ]
        fine_radius = scale + 2
    else:
        shortlist = [
            (dx, dy)
            for dx, dy, _ in _offset_candidates(
                template, frame, (box[0], box[1]), radius=radius, step=COARSE_STEP_PX,
                top=COARSE_CANDIDATES, prior=DISTANCE_PRIOR,
            )
        ]
        fine_radius = COARSE_STEP_PX

    medium: list[tuple[int, int, float]] = []
    for cdx, cdy in shortlist:
        fdx, fdy, ratio = _offset_candidates(
            template, frame, (box[0] + cdx, box[1] + cdy), radius=fine_radius, step=2,
            prior=DISTANCE_PRIOR, prior_from=(cdx, cdy), prior_radius=radius,
        )[0]
        medium.append((cdx + fdx, cdy + fdy, ratio))
    medium.sort(key=lambda c: c[2])

    refined: list[tuple[int, int, float]] = []
    for cdx, cdy, _ in medium[:FINE_CANDIDATES]:
        fdx, fdy, ratio = _offset_candidates(
            template, frame, (box[0] + cdx, box[1] + cdy), radius=2, step=1,
            prior=DISTANCE_PRIOR, prior_from=(cdx, cdy), prior_radius=radius,
        )[0]
        refined.append((cdx + fdx, cdy + fdy, ratio))
    # Pixels alone cannot separate a control from its twins — the cloth has three
    # rows of identical cells and two identical even-money bars — so the nearest
    # candidate wins unless a farther one is clearly, not marginally, better. The
    # penalty is a twentieth of a ratio across the whole search radius, which a
    # real drift of a few pixels does not notice.
    dx, dy, ratio = min(
        refined,
        key=lambda r: r[2] + DISTANCE_PRIOR * (abs(r[0]) + abs(r[1])) / (2 * max(1, radius)),
    )
    score = 1.0 - ratio
    if score < TEMPLATE_MIN_SCORE:
        return Relocation(button_id, box, None, 0, 0, score, "lost")
    if score < MOVE_MIN_SCORE:
        # Something is drawn over this control, so the best match is a look-alike
        # elsewhere. Keep the box, write nothing, and say what happened.
        return Relocation(button_id, box, box, 0, 0, score, "covered")
    moved = max(abs(dx), abs(dy)) >= MOVE_MIN_PX
    new_box = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
    return Relocation(
        button_id, box, new_box, dx, dy, score, "moved" if moved else "unchanged"
    )


def relocate_controls(
    baseline: np.ndarray,
    frame: np.ndarray,
    boxes: dict[str, Box],
    *,
    progress: ProgressFn = print,
) -> list[Relocation]:
    # Both frames are reduced and pooled once here rather than per control; the
    # cloth alone would otherwise redo it fifty times.
    space = _search_space(baseline, frame)
    out: list[Relocation] = []
    for bid, box in boxes.items():
        rel = _relocate(space, box, bid)
        if rel.status == "unchanged":
            note = "same place"
        elif rel.status == "covered":
            note = "something is drawn over it; box kept"
        else:
            note = f"dx={rel.dx} dy={rel.dy}"
        progress(f"  {bid}: {rel.status} ({note}, match {rel.score * 100:.0f}%)")
        out.append(rel)
    return out


# ---------------------------------------------------------------------------
# Discovery of controls nothing knows about yet
# ---------------------------------------------------------------------------


def _components(mask: np.ndarray, min_px: int) -> list[Box]:
    """Bounding boxes of the mask's connected blobs, four-way."""
    seen = np.zeros(mask.shape, dtype=bool)
    boxes: list[Box] = []
    h, w = mask.shape
    for sy in range(h):
        row = mask[sy]
        for sx in range(w):
            if not row[sx] or seen[sy, sx]:
                continue
            queue = deque([(sy, sx)])
            seen[sy, sx] = True
            x0 = x1 = sx
            y0 = y1 = sy
            count = 0
            while queue:
                cy, cx = queue.popleft()
                count += 1
                x0, x1 = min(x0, cx), max(x1, cx)
                y0, y1 = min(y0, cy), max(y1, cy)
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((ny, nx))
            if count >= min_px:
                boxes.append((x0, y0, x1 + 1, y1 + 1))
    return boxes


def _overlaps(a: Box, b: Box) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _pool_max(mask: np.ndarray, k: int) -> np.ndarray:
    h = mask.shape[0] // k * k
    w = mask.shape[1] // k * k
    return mask[:h, :w].reshape(h // k, k, w // k, k).max(axis=(1, 3))


def _grow(mask: np.ndarray, rx: int, ry: int) -> np.ndarray:
    out = mask.copy()
    for dx in range(-rx, rx + 1):
        for dy in range(-ry, ry + 1):
            out |= np.roll(np.roll(mask, dx, axis=1), dy, axis=0)
    return out


def discover_controls(
    frame: np.ndarray,
    base_ref: np.ndarray | None,
    occupied: Sequence[Box] = (),
    *,
    volatile: np.ndarray | None = None,
    limit: int = DISCOVERY_MAX_BOXES,
) -> list[Box]:
    """
    Areas the open screen draws that no mapped control covers — as hints, not facts.

    Comparing raw pixels with the base cloth is useless here: opening a panel dims
    the entire game behind it, so every pixel "changes" and the whole screen comes
    back as one candidate. What actually distinguishes a panel is that it puts *new
    edges* where the cloth had none, and dimming barely moves those. So the two
    frames are reduced to edge maps, the new edges are pooled and grown so the words
    of one caption merge into one box, and the game's own volatile chrome is masked
    out — without that mask a plain phase change alone offers ten candidates.

    Even then this is a suggestion: a lit neighbour pill produces new edges too.
    Callers must have a human confirm before anything reaches the registry.
    """
    if base_ref is None or base_ref.shape != frame.shape:
        return []
    step = DISCOVERY_SCALE
    before = _pool_max(_gradient(_luma(base_ref)) > EDGE_DELTA, step)
    after = _pool_max(_gradient(_luma(frame)) > EDGE_DELTA, step)
    mask = after & ~before
    if volatile is not None and volatile.shape == frame.shape[:2]:
        mask &= ~_pool_max(volatile, step)
    blobs = _components(_grow(mask, MERGE_X, MERGE_Y), max(4, DISCOVERY_MIN_PX // (step * step)))
    out: list[Box] = []
    for x0, y0, x1, y1 in sorted(
        blobs, key=lambda b: (b[3] - b[1]) * (b[2] - b[0]), reverse=True
    ):
        box = (x0 * step, y0 * step, x1 * step, y1 * step)
        if any(_overlaps(box, taken) for taken in occupied):
            continue
        out.append(box)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Registry writes
# ---------------------------------------------------------------------------


def _hitbox_fields(
    box: Box, *, client: tuple[int, int], kind: str, overlay: str = ""
) -> dict[str, Any]:
    cw, ch = client
    x0, y0, x1, y1 = box
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    cx, cy = x0 + w // 2, y0 + h // 2
    out: dict[str, Any] = {
        "x": x0,
        "y": y0,
        "width": w,
        "height": h,
        "click_center_px": {"x": cx, "y": cy},
        "click_center_pct": {
            "x_pct": round(100.0 * cx / cw, 3),
            "y_pct": round(100.0 * cy / ch, 3),
        },
        "click_area_pct": area_pct_dict(
            100.0 * x0 / cw, 100.0 * y0 / ch, 100.0 * x1 / cw, 100.0 * y1 / ch
        ),
        "kind": kind,
        "client": {"width": cw, "height": ch},
    }
    if overlay:
        out["overlay"] = overlay
    return out


def candidate_ids(screen_key: str, count: int, taken: Iterable[str] = ()) -> list[str]:
    """Ids for freshly discovered controls: ``HELP_V2_BTN01`` and friends."""
    prefix = re.sub(r"[^A-Z0-9]+", "_", screen_key.upper()).strip("_") or "SCREEN"
    used = set(taken)
    out: list[str] = []
    n = 1
    while len(out) < count:
        bid = f"{prefix}_BTN{n:02d}"
        n += 1
        if bid in used:
            continue
        used.add(bid)
        out.append(bid)
    return out


def apply_to_registry(
    *,
    layout_id: str,
    screen_key: str,
    screen_name: str,
    kind: str,
    relocations: Sequence[Relocation],
    new_boxes: dict[str, Box] | None = None,
    client: tuple[int, int],
    hitboxes_path: Path | None = None,
) -> dict[str, Any]:
    """Fold moved boxes and newly named controls into the skin's registry."""
    lid = _lid(layout_id)
    path = hitboxes_path or registry_path(lid)
    data = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    buttons = data.setdefault("buttons", {})
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    overlay = screen_key if kind == "overlay" else ""

    moved: list[str] = []
    for rel in relocations:
        if rel.status != "moved" or rel.new_box is None:
            continue
        entry = buttons.get(rel.button_id)
        entry_kind = (entry or {}).get("kind") or ("overlay" if overlay else "bet")
        fields = _hitbox_fields(rel.new_box, client=client, kind=entry_kind, overlay=overlay)
        if isinstance(entry, dict):
            entry.update(fields)
        else:
            entry = fields
            buttons[rel.button_id] = entry
        entry["remapped"] = {
            "method": "screen_map",
            "screen": screen_key,
            "dx": rel.dx,
            "dy": rel.dy,
            "match": round(rel.score, 4),
            "checked_at": stamp,
        }
        moved.append(rel.button_id)

    added: list[str] = []
    for bid, box in (new_boxes or {}).items():
        entry = _hitbox_fields(box, client=client, kind="overlay" if overlay else "bet",
                               overlay=overlay)
        entry["proof"] = "geometry discovered by screen map; effect not verified yet"
        entry["remapped"] = {
            "method": "screen_map",
            "screen": screen_key,
            "discovered": True,
            "checked_at": stamp,
        }
        buttons[bid] = entry
        added.append(bid)

    if overlay:
        screens = data.setdefault("screens", {})
        info = screens.setdefault(screen_key, {})
        info["name"] = screen_name or info.get("name") or _pretty(screen_key)
        info["kind"] = kind
        info["mapped_at"] = stamp
    status = data.setdefault("status", {})
    status["screen_map_last"] = {
        "screen": screen_key,
        "moved": len(moved),
        "added": len(added),
        "checked_at": stamp,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    invalidate_hitbox_cache(path)
    return {"path": str(path), "moved": sorted(moved), "added": sorted(added)}


def save_candidate_review(
    capture_path: Path, candidates: dict[str, Box], dest: Path
) -> Path | None:
    """
    Draw the discovered candidate boxes over the capture, for someone to look at.

    Candidates are never written to the registry on their own word, so the operator has
    to be able to see what was found rather than read thirteen sets of coordinates.
    """
    if not candidates:
        return None
    from PIL import Image, ImageDraw

    image = Image.open(capture_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    for bid, (x0, y0, x1, y1) in candidates.items():
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=(255, 96, 0), width=3)
        label = bid.rsplit("_", 1)[-1]
        draw.text((x0 + 5, max(0, y0 - 14)), label, fill=(255, 196, 0))
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    return dest


# How each control is drawn on the review image. Ordered as the legend reads.
REVIEW_COLOURS: dict[str, tuple[int, int, int]] = {
    "unchanged": (64, 200, 96),
    "moved": (0, 190, 255),
    "kept": (96, 170, 255),
    "covered": (150, 150, 150),
    "featureless": (150, 110, 210),
    "lost": (255, 64, 64),
    "candidate": (255, 150, 0),
    "new": (255, 200, 0),
}
REVIEW_MEANING = {
    "unchanged": "found where it was mapped",
    "moved": "found somewhere else; registry updated",
    "kept": "kept as mapped (no baseline to compare)",
    "covered": "hidden behind something; left alone",
    "featureless": "nothing drawn there to match",
    "lost": "not found in this capture",
    "candidate": "discovered, needs a human before it counts",
    "new": "written as a new control",
}


def render_map_review(
    *,
    capture_path: Path,
    dest: Path,
    layout_id: str,
    screen_key: str,
    screen_name: str,
    boxes: dict[str, Box],
    relocations: Sequence[Relocation] = (),
    candidates: dict[str, Box] | None = None,
    accepted: bool = False,
    target_label: str = "",
    registry: dict[str, Any] | None = None,
) -> Path:
    """
    Draw the whole mapped screen over the capture it was mapped from.

    This is the artifact an operator actually judges the run by: coordinates in a
    log say nothing about whether a box sits on its control, and a mapper that
    quietly moved something to a look-alike is only obvious in a picture.
    """
    from PIL import Image, ImageDraw

    image = Image.open(capture_path).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    width, height = image.size
    font = _review_font(15)
    small = _review_font(13)

    by_id = {r.button_id: r for r in relocations}
    counts: dict[str, int] = {}

    def status_of(bid: str) -> str:
        rel = by_id.get(bid)
        if rel is None:
            return "kept"
        return rel.status if rel.status in REVIEW_COLOURS else "kept"

    for bid, box in sorted(boxes.items()):
        status = status_of(bid)
        rel = by_id.get(bid)
        drawn = rel.new_box if (rel and rel.new_box) else box
        colour = REVIEW_COLOURS[status]
        counts[status] = counts.get(status, 0) + 1
        x0, y0, x1, y1 = drawn
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=colour, width=2)
        if status == "moved" and rel is not None:
            ox0, oy0, ox1, oy1 = rel.old_box
            draw.rectangle((ox0, oy0, ox1 - 1, oy1 - 1), outline=(*colour, 110), width=1)
            draw.line(
                (
                    (ox0 + ox1) // 2,
                    (oy0 + oy1) // 2,
                    (x0 + x1) // 2,
                    (y0 + y1) // 2,
                ),
                fill=colour,
                width=2,
            )
        _review_tag(draw, bid, x0, y0, colour, small)

    for bid, box in sorted((candidates or {}).items()):
        status = "new" if accepted else "candidate"
        counts[status] = counts.get(status, 0) + 1
        colour = REVIEW_COLOURS[status]
        x0, y0, x1, y1 = box
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=colour, width=3)
        _review_tag(draw, bid.rsplit("_", 1)[-1], x0, y0, colour, small)

    written = registry or {}
    head = [
        f"{screen_name}  ({layout_id} / {screen_key})",
        f"mapped from {target_label or 'cabinet'} at {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"{len(boxes)} mapped controls"
        + (f", {len(candidates or {})} candidate" if candidates else "")
        + (
            f" — registry: {len(written.get('moved') or [])} moved, "
            f"{len(written.get('added') or [])} added"
            if written
            else " — registry not written"
        ),
    ]
    _review_banner(draw, head, width, font)
    _review_legend(draw, counts, height, small)

    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    return dest


def _review_font(size: int):
    from PIL import ImageFont

    for name in ("segoeuib.ttf", "arialbd.ttf", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _review_tag(draw, text: str, x: int, y: int, colour, font) -> None:
    """Label a box just above it, on a plate so it stays readable on any pixels."""
    label = text if len(text) <= 18 else text[:17] + "…"
    try:
        x0, y0, x1, y1 = draw.textbbox((0, 0), label, font=font)
        tw, th = x1 - x0, y1 - y0
    except AttributeError:  # very old Pillow
        tw, th = len(label) * 7, 12
    ty = max(0, y - th - 4)
    draw.rectangle((x, ty, x + tw + 6, ty + th + 4), fill=(0, 0, 0, 170))
    draw.text((x + 3, ty + 2), label, fill=colour, font=font)


def _review_banner(draw, lines: Sequence[str], width: int, font) -> None:
    draw.rectangle((0, 0, width, 20 * len(lines) + 12), fill=(0, 0, 0, 190))
    for i, line in enumerate(lines):
        draw.text((12, 8 + 20 * i), line, fill=(235, 235, 235), font=font)


def _review_legend(draw, counts: dict[str, int], height: int, font) -> None:
    rows = [(k, counts.get(k, 0)) for k in REVIEW_COLOURS if counts.get(k)]
    if not rows:
        return
    box_h = 18 * len(rows) + 12
    top = height - box_h - 8
    draw.rectangle((8, top, 430, top + box_h), fill=(0, 0, 0, 190))
    for i, (status, n) in enumerate(rows):
        y = top + 6 + 18 * i
        draw.rectangle((16, y + 3, 30, y + 13), fill=REVIEW_COLOURS[status])
        draw.text(
            (38, y),
            f"{n} {status} — {REVIEW_MEANING[status]}",
            fill=(225, 225, 225),
            font=font,
        )


def save_baseline(layout_id: str, screen_key: str, capture_path: Path) -> Path:
    """Promote this run's capture to the screen's baseline for next time."""
    dest = baseline_path(layout_id, screen_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(Path(capture_path).read_bytes())
    return dest


# ---------------------------------------------------------------------------
# Live capture + verification
# ---------------------------------------------------------------------------


def capture_current_screen(
    where: "Target | str",
    dest: Path,
    *,
    settle_ms: int = 1600,
    tries: int = 3,
    progress: ProgressFn = lambda _line: None,
    session: "Session | None" = None,
) -> Path:
    """
    Screenshot the target's screen once the picture has stopped moving.

    Panels on this game arrive a second or two after the click that opened them, and a
    single screenshot regularly catches the screen the operator just left: the menu rail
    was photographed closed twice while it was in fact opening, which would have written
    the rail into the cloth's baseline. Two captures in a row have to agree before the
    frame is used for anything.

    *where* is a :class:`~automation.roulette_target.Target` or a bare cabinet IP.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    sess = session or open_session(as_target(where))
    previous: np.ndarray | None = None
    shot = Path(dest)
    for attempt in range(1, max(1, tries) + 1):
        ok, detail = sess.capture(dest, settle_ms=settle_ms)
        if not ok:
            raise RuntimeError(f"capture failed: {detail}")
        shot = Path(detail)
        frame = load_frame(shot)
        if previous is not None and _identity_distance(previous, frame) <= SETTLED_MAX:
            return shot
        if attempt < tries:
            progress("  the screen is still moving; waiting for it to settle")
        previous = frame
    return shot


def detect_current_screen(
    where: Target | str, layout_id: str | None = None, *, work: Path | None = None
) -> dict[str, Any]:
    """Capture the open screen and rank it against everything the skin knows."""
    lid = _lid(layout_id)
    target = as_target(where)
    work = work or out_root() / lid / "_detect"
    shot = capture_current_screen(target, work / "current.png")
    frame = load_frame(shot)
    matches = identify_screen(frame, lid)
    best, confident = suggest_screen(matches)
    return {
        "ip": target.ip,
        "target": target.label,
        "target_kind": target.kind,
        "layout_id": lid,
        "capture": str(shot),
        "skin_fit": dict(identify_skin(frame)),
        "skin_mismatch": skin_mismatch(frame, lid),
        "matches": [m.as_dict() for m in matches],
        "suggested": best.key if best else "",
        "suggested_name": best.name if best else "",
        "confident": confident,
        "screens": [
            {
                "key": s.key,
                "name": s.name,
                "kind": s.kind,
                "state": s.state,
                "label": s.label,
                "controls": len(s.control_ids),
                "mapped": len(s.mapped_ids),
                "has_baseline": s.baseline is not None,
            }
            for s in known_screens(lid)
        ],
    }


def _verify_cloth(
    ip: str, layout_id: str, *, targets: list[str], progress: ProgressFn
) -> dict[str, Any]:
    """
    Prove a cloth screen through the middleware, with the three-role session.

    Sniff has to be running while the chip is placed, so it goes on its own
    thread; verify then joins the two halves and rejects credit-only evidence.
    """
    from automation.roulette_map_session import create_session, role_place, role_sniff, role_verify

    session = create_session(ip=ip, layout_id=layout_id, targets=targets)
    progress(f"  middleware session {session.name} on {', '.join(targets)}")
    sniff: dict[str, Any] = {}
    error: list[str] = []

    def _sniff() -> None:
        try:
            sniff.update(role_sniff(session))
        except Exception as exc:  # a failed sniff must not strand the place role
            error.append(f"sniff: {exc}")

    thread = threading.Thread(target=_sniff, name="screen-map-sniff", daemon=True)
    thread.start()
    try:
        place = role_place(session)
    finally:
        thread.join(timeout=180.0)
    verdict = role_verify(session)
    return {
        "oracle": "middleware",
        "session": str(session),
        "placed": place.get("ok"),
        "verdict": verdict.get("verdict") or verdict,
        "errors": error,
    }


def _verify_overlay(
    ip: str, layout_id: str, ids: Sequence[str], *, progress: ProgressFn
) -> dict[str, Any]:
    """Prove an overlay screen's controls with the pixel-diff sweep."""
    from automation.roulette_verify_ui import apply_results_to_hitboxes, ui_targets, verify_ui

    available = set(ui_targets(layout_id))
    wanted = [bid for bid in ids if bid in available]
    if not wanted:
        progress("  no probeable controls on this screen; skipping verification")
        return {"oracle": "pixel_diff", "tested": 0, "skipped": "no probeable controls"}
    progress(f"  pixel-diff probes: {', '.join(wanted)}")
    summary = verify_ui(ip, layout_id=layout_id, ids=wanted, progress=progress)
    folded = apply_results_to_hitboxes(layout_id=layout_id)
    return {
        "oracle": "pixel_diff",
        "tested": summary.get("tested"),
        "with_effect": summary.get("with_effect"),
        "inert": summary.get("inert"),
        "errors": summary.get("errors"),
        "aborted": summary.get("aborted"),
        "folded": folded.get("updated"),
    }


def _verify_locally(
    session: Session,
    layout_id: str,
    ids: Sequence[str],
    *,
    boxes: dict[str, Box],
    new_boxes: dict[str, Box] | None = None,
    progress: ProgressFn,
    limit: int = 6,
) -> dict[str, Any]:
    """
    Prove a locally mapped screen by clicking centres and watching the pixels.

    The cabinet sweeps (three-role middleware session, UI pixel sweep) are wired
    for WinRM, so a local run gets the same idea in one process: click the middle
    of a control, screenshot, and see whether anything changed. It answers "is
    this box on something that reacts", which is what a fresh local map needs.
    """
    from automation.roulette_edge_probe import click_changes_screen

    pool = dict(boxes)
    pool.update(new_boxes or {})
    wanted = [bid for bid in ids if bid in pool][:limit]
    if not wanted:
        progress("  nothing clickable to probe locally; skipping verification")
        return {"oracle": "pixel_diff_local", "tested": 0, "skipped": "no mapped controls"}
    reacted: list[str] = []
    inert: list[str] = []
    for bid in wanted:
        x0, y0, x1, y1 = pool[bid]
        changed, delta = click_changes_screen(
            session, ((x0 + x1) // 2, (y0 + y1) // 2), layout_id=layout_id
        )
        (reacted if changed else inert).append(bid)
        progress(f"  {bid}: {'reacted' if changed else 'no visible change'} (delta {delta:.4f})")
    return {
        "oracle": "pixel_diff_local",
        "tested": len(wanted),
        "with_effect": reacted,
        "inert": inert,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class RemapResult:
    ok: bool
    layout_id: str
    screen_key: str
    screen_name: str
    kind: str
    is_new: bool
    capture: str = ""
    out_dir: str = ""
    matches: list[dict[str, Any]] = field(default_factory=list)
    relocations: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    registry: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    baseline: str = ""
    review: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "layout_id": self.layout_id,
            "screen": self.screen_key,
            "screen_name": self.screen_name,
            "kind": self.kind,
            "is_new": self.is_new,
            "capture": self.capture,
            "out_dir": self.out_dir,
            "matches": self.matches,
            "relocations": self.relocations,
            "candidates": self.candidates,
            "registry": self.registry,
            "verification": self.verification,
            "baseline": self.baseline,
            "review": self.review,
            "message": self.message,
        }


def remap_current_screen(
    *,
    ip: str = "",
    target: Target | None = None,
    layout_id: str = DEFAULT_LAYOUT,
    screen_key: str = "",
    screen_name: str = "",
    is_new: bool = False,
    apply_registry: bool = True,
    verify: bool = True,
    accept_candidates: bool = False,
    cloth_targets: Sequence[str] = ("1", "17"),
    progress: ProgressFn = print,
) -> RemapResult:
    """
    Map the screen that is open on the cabinet right now.

    The capture happens first and once, so the operator only has to hold the
    screen still for that; relocation and discovery work off the image, and the
    verification that follows is free to navigate.

    Pass *target* to map this machine's own screen; *ip* remains for cabinets.
    """
    where = target or cabinet_target(ip)
    lid = _lid(layout_id)
    surface = surface_for(lid)
    spec = None if is_new else screen_by_key(lid, screen_key)
    if not is_new and spec is None:
        raise KeyError(f"{lid} has no mapped screen {screen_key!r}")
    if is_new:
        screen_key = validate_new_screen_name(lid, screen_name or screen_key)
        screen_name = (screen_name or _pretty(screen_key)).strip()
    else:
        screen_name = screen_name or spec.name

    kind = "overlay" if is_new else spec.kind
    work = out_dir_for(lid, screen_key)
    work.mkdir(parents=True, exist_ok=True)

    session = open_session(where)
    progress(f"capturing the open screen on {session.describe()} ({lid}) ...")
    shot = capture_current_screen(
        where, work / "current.png", progress=progress, session=session
    )
    frame = load_frame(shot)
    size = _frame_size(frame)
    surface_size = (surface.client_w, surface.client_h)

    fits = ", ".join(f"{name}={fit:.2f}" for name, fit in identify_skin(frame))
    blind = skin_unreadable(frame, lid)
    note = " (no cloth in view, so the skin cannot be checked)" if blind else ""
    progress(f"  skin fit: {fits}{note}")
    matches = identify_screen(frame, lid)
    best, confident = suggest_screen(matches)
    top = ", ".join(f"{m.key}={m.score:.3f}{'*' if m.matched else ''}" for m in matches[:4])
    progress(f"  screen identity: {top or 'nothing to compare against'}")
    result = RemapResult(
        ok=False,
        layout_id=lid,
        screen_key=screen_key,
        screen_name=screen_name,
        kind=kind,
        is_new=is_new,
        capture=str(shot),
        out_dir=str(work),
        matches=[m.as_dict() for m in matches],
    )

    # The skin comes first, because getting it wrong poisons everything after it:
    # a layout2 run against a layout1 cloth saved the wrong baseline, offered the
    # phase differences between two skins as "new controls", and clicked layout2
    # coordinates on layout1 chrome — which is how .90 got swapped skins mid-test.
    wrong_skin = skin_mismatch(frame, lid)
    if wrong_skin:
        result.message = (
            f"Refusing to map {screen_key!r} as {lid}: {wrong_skin}. "
            "Pick the skin the cabinet is actually showing."
        )
        progress(f"  REFUSED: {result.message}")
        return result

    refusal = map_refusal(matches, screen_key, is_new=is_new)
    if refusal:
        result.message = refusal
        progress(f"  REFUSED: {result.message}")
        return result

    base_path = baseline_path(lid, "base")
    base_ref = load_frame(base_path) if base_path.is_file() else None
    if base_ref is not None and _frame_size(base_ref) != size:
        base_ref = None

    relocations: list[Relocation] = []
    boxes: dict[str, Box] = {}
    if not is_new:
        boxes = {
            bid: _scaled_box(box, surface_size, size)
            for bid, box in _boxes_for(spec, surface, lid).items()
        }
        if spec.baseline is None:
            progress("  no baseline for this screen yet; recording one, boxes kept as mapped")
        else:
            reference = load_frame(spec.baseline)
            if _frame_size(reference) != size:
                progress("  baseline was captured at another resolution; boxes kept as mapped")
            else:
                progress(f"  relocating {len(boxes)} mapped controls")
                relocations = relocate_controls(
                    _match_brightness(reference, frame), frame, boxes, progress=progress
                )
        result.relocations = [r.as_dict() for r in relocations]

    occupied = [r.new_box or r.old_box for r in relocations if r.status != "lost"]
    if not occupied and not is_new:
        occupied = list(
            _scaled_box(box, surface_size, size)
            for box in _boxes_for(spec, surface, lid).values()
        )
    volatile = None
    if kind == "overlay":
        from automation.roulette_verify_ui import slow_volatile_mask

        volatile = slow_volatile_mask(surface, frame.shape)
    candidates = (
        discover_controls(frame, base_ref, occupied, volatile=volatile)
        if kind == "overlay"
        else []
    )
    proposed: dict[str, Box] = {}
    if candidates:
        buttons = load_hitboxes(lid).get("buttons") or {}
        ids = candidate_ids(screen_key, len(candidates), taken=buttons.keys())
        proposed = dict(zip(ids, candidates))
        for bid, box in proposed.items():
            progress(f"  candidate control {bid} at {box}")
    result.candidates = [{"id": bid, "box": list(box)} for bid, box in proposed.items()]
    # Discovery guesses; it cannot tell a new button from a neighbour pill lighting
    # up. Its boxes become click targets for the bot the moment they are written, so
    # they stay out of the registry until someone has looked at them.
    new_boxes = proposed if accept_candidates else {}
    if proposed and not accept_candidates:
        review = save_candidate_review(Path(shot), proposed, work / "candidates.png")
        progress(
            f"  {len(proposed)} candidate controls listed for review (not written); "
            f"see {review}, then re-run with --accept-candidates to keep them"
        )

    moved = [r for r in relocations if r.status == "moved"]
    lost = [r for r in relocations if r.status == "lost"]
    blank = [r for r in relocations if r.status == "featureless"]
    covered = [r for r in relocations if r.status == "covered"]
    # A baseline that shows something else entirely (a stale capture of the admin
    # shell, a different skin) loses every control at once. Nothing about that is
    # per-control, and guessing new boxes from it would be worse than saying so.
    evidence = len(relocations) - len(blank)
    stale_baseline = evidence > 2 and len(lost) == evidence
    if stale_baseline:
        progress(
            "  none of the mapped controls were found: the stored baseline does not show "
            "this screen. Geometry left as it was; the baseline is refreshed from this capture."
        )
        relocations, moved, lost, blank, covered = [], [], [], [], []
        result.relocations = []
    if apply_registry and (moved or new_boxes or is_new):
        result.registry = apply_to_registry(
            layout_id=lid,
            screen_key=screen_key,
            screen_name=screen_name,
            kind=kind,
            relocations=relocations,
            new_boxes=new_boxes,
            client=size,
        )
        progress(
            f"  registry: {len(result.registry.get('moved') or [])} moved, "
            f"{len(result.registry.get('added') or [])} added"
        )
    elif apply_registry:
        progress("  registry unchanged: nothing moved and nothing new")

    if apply_registry:
        # A panel drawn over the cloth would be baked into the cloth's own reference,
        # and every control behind it would match the panel instead of itself from then
        # on. The panel's own baseline is fine to take: that is what it is a picture of.
        open_panels = [
            m for m in matches if m.matched and m.basis == "baseline" and m.key != screen_key
        ]
        if kind == "cloth" and open_panels:
            names = ", ".join(m.name for m in open_panels)
            progress(f"  baseline kept as it was: {names} is open over this screen")
        else:
            result.baseline = str(save_baseline(lid, screen_key, Path(shot)))
            progress(f"  baseline saved: {result.baseline}")

    # The picture of the finished map, always: an operator judges a mapping run by
    # looking at it, not by reading coordinates out of the log.
    try:
        result.review = str(
            render_map_review(
                capture_path=Path(shot),
                dest=work / "mapped.png",
                layout_id=lid,
                screen_key=screen_key,
                screen_name=screen_name,
                boxes=boxes,
                relocations=relocations,
                candidates=proposed,
                accepted=bool(new_boxes) and accept_candidates,
                target_label=session.describe(),
                registry=result.registry,
            )
        )
        progress(f"  map picture: {result.review}")
    except Exception as exc:  # noqa: BLE001 -- a failed drawing must not lose the map
        progress(f"  could not draw the map picture: {exc}")

    if verify:
        progress("verifying the screen (it may navigate now) ...")
        try:
            if where.is_local:
                ids = list(spec.control_ids) if spec else []
                ids += list(new_boxes)
                result.verification = _verify_locally(
                    session, lid, ids, boxes=boxes, new_boxes=new_boxes, progress=progress
                )
            elif kind == "cloth":
                result.verification = _verify_cloth(
                    where.ip, lid, targets=list(cloth_targets), progress=progress
                )
            else:
                ids = list(spec.control_ids) if spec else []
                ids += [bid for bid in new_boxes]
                result.verification = _verify_overlay(where.ip, lid, ids, progress=progress)
        except Exception as exc:  # mapping already succeeded; say so and keep it
            result.verification = {"error": str(exc)}
            progress(f"  verification failed: {exc}")

    result.ok = True
    bits = [f"{len(moved)} moved", f"{len(new_boxes)} new", f"{len(lost)} lost"]
    if blank:
        bits.append(f"{len(blank)} not drawn")
    if covered:
        bits.append(f"{len(covered)} hidden behind something")
    if stale_baseline:
        bits.append("baseline refreshed (it showed another screen)")
    result.message = f"{screen_name} ({screen_key}): " + ", ".join(bits)
    (work / "result.json").write_text(
        json.dumps(result.as_dict(), indent=2) + "\n", encoding="utf-8"
    )
    progress(f"wrote {work / 'result.json'}")
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Map the roulette screen that is open now")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument(
        "--target",
        default="",
        help="'local' to map this machine's own screen, or a cabinet IP (defaults to --ip)",
    )
    p.add_argument("--layout", default=DEFAULT_LAYOUT, choices=KNOWN_LAYOUTS)
    p.add_argument("--detect", action="store_true", help="Identify the open screen and exit")
    p.add_argument("--list", action="store_true", help="List known screens and exit")
    p.add_argument("--screen", default="", help="Key of the screen being re-mapped")
    p.add_argument("--new-screen", default="", help="Name for a screen that is not mapped yet")
    p.add_argument("--apply", action="store_true", help="Write results into the registry")
    p.add_argument(
        "--accept-candidates",
        action="store_true",
        help="Also write the discovered candidate controls (review them first)",
    )
    p.add_argument("--no-verify", action="store_true", help="Skip verification")
    args = p.parse_args(argv)

    if args.list:
        for spec in known_screens(args.layout):
            print(f"{spec.kind:<8} {spec.key:<14} {spec.label}")
        return 0

    from automation.roulette_target import parse_target

    where = parse_target(args.target or args.ip)
    if args.detect:
        print(json.dumps(detect_current_screen(where, args.layout), indent=2))
        return 0
    if not args.screen and not args.new_screen:
        p.error("give --screen <key>, --new-screen <name>, --detect or --list")

    result = remap_current_screen(
        target=where,
        layout_id=args.layout,
        screen_key=args.screen,
        screen_name=args.new_screen,
        is_new=bool(args.new_screen),
        apply_registry=args.apply,
        accept_candidates=args.accept_candidates,
        verify=not args.no_verify,
    )
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
