
"""
Slot Roulette LAST 100 history panel + hot/cold frequency rules.

`HISTORY_BTN` opens a modal titled HISTORY / LAST 100 NUMBERS (5×20 chips,
newest at top-left). `HISTORY_CLOSE` dismisses it.

Hot / cold (matches the cloth flash + mid-panel capsules on .90)::

    hot  = 5 numbers with highest count in the last 100
           ties → least recently seen (older in the newest-first list)
    cold = 5 numbers with lowest count (0 = never in the window)
           ties → most recently seen

Use :func:`hot_cold_from_history` in automated tests, and
:func:`read_history_numbers` once a HISTORY panel screenshot is available.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageDraw

from automation.roulette_slot_areas import PANEL_Y
from automation.roulette_slot_hotcold import HotColdSets

HISTORY_COUNT = 100
HOT_COUNT = 5
COLD_COUNT = 5

# Panel-local boxes on the bottom 1920×1080 cloth panel (y before +PANEL_Y).
HISTORY_PANEL_P = (160, 80, 1760, 980)
HISTORY_CLOSE_P = (880, 790, 1040, 870)
# 5×20 chip grid — calibrated on 2026-07-29 HISTORY capture (01_bot.png)
# against last100_fixture.json (99/100 self-hit).
HISTORY_GRID_ORIGIN_P = (265.0, 320.0)  # centre of cell (0,0)
HISTORY_GRID_GAP_X = 76.0
HISTORY_GRID_GAP_Y = 86.0
HISTORY_CELL_R = 30
PANEL_TEMPLATES_NPZ = Path("_tmp_logs/slot_roulette/history/panel_templates.npz")


def _lift(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return (x0, y0 + PANEL_Y, x1, y1 + PANEL_Y)


HISTORY_PANEL = _lift(HISTORY_PANEL_P)
HISTORY_CLOSE = _lift(HISTORY_CLOSE_P)


@dataclass(frozen=True)
class HistoryRead:
    numbers: list[int]  # newest first, length <= 100
    source: str = "panel"


def hot_cold_from_history(
    numbers_newest_first: Sequence[int],
    *,
    hot_count: int = HOT_COUNT,
    cold_count: int = COLD_COUNT,
    universe: range = range(37),
) -> HotColdSets:
    """
    Derive hot/cold sets from a newest-first spin history.

    European single-zero: universe is `0..36` (no `00`).
    """
    nums = [int(n) for n in numbers_newest_first]
    if len(nums) > HISTORY_COUNT:
        nums = nums[:HISTORY_COUNT]
    counts = Counter(nums)
    first_idx: dict[int, int] = {}
    for i, n in enumerate(nums):
        first_idx.setdefault(n, i)
    absent_idx = len(nums)  # never seen → older than anything in the window

    def hot_key(n: int) -> tuple:
        return (-counts.get(n, 0), -(first_idx.get(n, absent_idx)), n)

    def cold_key(n: int) -> tuple:
        return (counts.get(n, 0), first_idx.get(n, absent_idx), n)

    hot = frozenset(str(n) for n in sorted(universe, key=hot_key)[:hot_count])
    cold = frozenset(str(n) for n in sorted(universe, key=cold_key)[:cold_count])
    return HotColdSets(hot=hot, cold=cold, source="history_freq", captured_at=time.time())


def _cell_centers_panel() -> list[tuple[float, float]]:
    x0, y0 = HISTORY_GRID_ORIGIN_P
    out: list[tuple[float, float]] = []
    for row in range(5):
        for col in range(20):
            out.append((x0 + col * HISTORY_GRID_GAP_X, y0 + row * HISTORY_GRID_GAP_Y))
    return out


def _classify_chip_color(crop: np.ndarray) -> str:
    r, g, b = crop[:,:,0].astype(np.float32), crop[:,:,1].astype(np.float32), crop[:,:,2].astype(np.float32)
    if float((g > 110) & (g > r + 25) & (g > b + 25)).mean() > 0.08:
        return "green"
    if float((r > 140) & (r > g + 40) & (r > b + 40)).mean() > 0.08:
        return "red"
    return "black"


def _vec(crop: Image.Image, size: int = 28) -> np.ndarray:
    g = np.asarray(crop.convert("L"), dtype=np.float32)
    g = np.asarray(
        Image.fromarray(g.astype(np.uint8)).resize((size, size), Image.BILINEAR),
        dtype=np.float32,
    )
    g = (g - g.mean()) / (g.std() + 1e-6)
    return g.ravel()


def load_panel_templates() -> dict[str, np.ndarray]:
    """History-chip templates (preferred) with cloth-cell fallback for gaps."""
    from automation.roulette_slot_areas import NUMBER_AREAS_PX

    templates: dict[str, np.ndarray] = {}
    if PANEL_TEMPLATES_NPZ.is_file():
        data = np.load(PANEL_TEMPLATES_NPZ)
        templates = {k: data[k] for k in data.files}
    cloth_path = Path("_tmp_logs/slot_roulette/play_client.png")
    if cloth_path.is_file():
        cloth = Image.open(cloth_path).convert("RGB")
        for name, box in NUMBER_AREAS_PX.items():
            if name in templates:
                continue
            x0, y0, x1, y1 = box
            if cloth.height >= 3000:
                crop = cloth.crop((x0, y0, x1, y1))
            else:
                crop = cloth.crop((x0, y0 - PANEL_Y, x1, y1 - PANEL_Y))
            templates[name] = _vec(crop)
    return templates


def read_history_numbers(
    image: Image.Image | Path | str,
    *,
    templates: dict[str, np.ndarray] | None = None,
    min_score: float = 0.25,
) -> HistoryRead:
    """
    Read the 5×20 LAST 100 grid from a bottom-panel or full-client capture.

    Prefer *panel* chip templates (`panel_templates.npz`); cloth cells are only
    a fallback for numbers never seen in the calibration fixture.
    """
    if isinstance(image, (str, Path)):
        im = Image.open(image).convert("RGB")
    else:
        im = image.convert("RGB")
    # Accept full 3240 client or bot panel 1080.
    if im.height >= 3000:
        bot = im.crop((0, PANEL_Y, 1920, PANEL_Y + 1080))
    elif im.height >= 2000:
        bot = im.crop((0, im.height - 1080, 1920, im.height))
    else:
        bot = im.crop((0, 0, min(1920, im.width), min(1080, im.height)))

    if templates is None:
        templates = load_panel_templates()
    if not templates:
        raise RuntimeError("no history panel templates available")

    nums: list[int] = []
    scores: list[float] = []
    r = HISTORY_CELL_R
    for cx, cy in _cell_centers_panel():
        box = (int(cx - r), int(cy - r), int(cx + r), int(cy + r))
        v = _vec(bot.crop(box))
        best_name, best_score = "0", -1.0
        for name, tv in templates.items():
            score = float(np.dot(v, tv) / (np.linalg.norm(v) * np.linalg.norm(tv) + 1e-9))
            if score > best_score:
                best_score, best_name = score, name
        nums.append(int(best_name))
        scores.append(best_score)
    mean_score = float(sum(scores) / max(1, len(scores)))
    source = "panel_ocr" if mean_score >= min_score else "panel_ocr_low_conf"
    return HistoryRead(numbers=nums, source=source)


def open_history_steps(*, focus: str = "OneHand") -> list[dict]:
    from automation.roulette_layout_store import resolve_hitbox_center
    t = resolve_hitbox_center("HISTORY_BTN", layout_id="slot")
    return [
        {"type": "focus_process", "value": focus, "ms": 350},
        {"type": "click", "value": t.as_spec(focus), "ms": 1000},
    ]


def close_history_steps(*, focus: str = "OneHand") -> list[dict]:
    from automation.roulette_layout_store import resolve_hitbox_center
    t = resolve_hitbox_center("HISTORY_CLOSE", layout_id="slot")
    if t is None:
        # Fallback percent of CLOSE box centre.
        x0, y0, x1, y1 = HISTORY_CLOSE
        spec = f"{focus}@{100*(x0+x1)/2/1920:.2f},{100*(y0+y1)/2/3240:.2f}"
    else:
        spec = t.as_spec(focus)
    return [
        {"type": "focus_process", "value": focus, "ms": 300},
        {"type": "click", "value": spec, "ms": 800},
    ]


__all__ = [
    "COLD_COUNT",
    "HISTORY_CLOSE",
    "HISTORY_COUNT",
    "HISTORY_PANEL",
    "HOT_COUNT",
    "HistoryRead",
    "close_history_steps",
    "hot_cold_from_history",
    "load_panel_templates",
    "open_history_steps",
    "read_history_numbers",
]
