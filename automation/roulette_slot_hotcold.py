
"""
Slot Roulette hot / cold numbers for bot stake selection.

The flame|snowflake pill under SHOW WINS flashes **5 hot** (fire) and **5 cold**
(ice) highlights on the square cloth for about **5 seconds**. The mid-panel
HOT / COLD capsules show the same lists persistently; the cloth flash is the
reliable visual proof when automating.

Typical bot flow::

    from automation.roulette_slot_hotcold import reveal_and_read_hot_cold
    sets = reveal_and_read_hot_cold(ip="10.0.0.90", agent=agent)
    # sets.hot / sets.cold  ->  frozenset[str] of "1".."36"
    # then click those straight boxes and SPIN
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

import numpy as np
from PIL import Image

from automation.roulette_slot_areas import NUMBER_AREAS_PX, PANEL_Y, UI_AREAS_PX

Mode = Literal["hot", "cold", "both"]

# One pill — flame + snowflake. Prefer this over the split half-boxes.
HOT_COLD_BUTTON = "HOT_COLD"
HOT_COLD_DURATION_S = 5.0
HOT_COUNT = 5
COLD_COUNT = 5


@dataclass(frozen=True)
class HotColdSets:
    hot: frozenset[str]
    cold: frozenset[str]
    source: str = "cloth"
    captured_at: float = 0.0

    def targets(self, mode: Mode = "hot") -> frozenset[str]:
        if mode == "hot":
            return self.hot
        if mode == "cold":
            return self.cold
        return self.hot | self.cold


def hot_cold_click_center_px() -> tuple[int, int]:
    """Full-client pixel centre of the flame|snowflake pill."""
    if HOT_COLD_BUTTON in UI_AREAS_PX:
        x0, y0, x1, y1 = UI_AREAS_PX[HOT_COLD_BUTTON]
    else:
        # Fallback: midpoint of the old half-boxes.
        a = UI_AREAS_PX["HOT_TOGGLE"]
        b = UI_AREAS_PX["COLD_TOGGLE"]
        x0, y0 = min(a[0], b[0]), min(a[1], b[1])
        x1, y1 = max(a[2], b[2]), max(a[3], b[3])
    return (x0 + x1) // 2, (y0 + y1) // 2


def hot_cold_click_spec(focus: str = "OneHand") -> str:
    cx, cy = hot_cold_click_center_px()
    return f"{focus}@{100.0 * cx / 1920:.2f},{100.0 * cy / 3240:.2f}"


def _cell_scores(img: np.ndarray, box: tuple[int, int, int, int]) -> tuple[float, float]:
    x0, y0, x1, y1 = box
    c = img[y0:y1, x0:x1]
    r, g, b = c[:, :, 0], c[:, :, 1], c[:, :, 2]
    hot = float(((r > 140) & (g > 60) & (g < 200) & (b < 90) & (r > b + 50)).mean())
    cold = float(((b > 140) & (r < 110) & (g > 80) & (b > r + 40)).mean())
    return hot, cold


def read_hot_cold_from_cloth(
    during: Image.Image | np.ndarray | Path | str,
    *,
    before: Image.Image | np.ndarray | Path | str | None = None,
    hot_count: int = HOT_COUNT,
    cold_count: int = COLD_COUNT,
) -> HotColdSets:
    """
    Rank every straight cell by fire vs ice glow.

    Pass a pre-click frame as *before* so residual felt colour is subtracted —
    required for a clean top-5 on live cabinets.
    """

    def _arr(x: Image.Image | np.ndarray | Path | str) -> np.ndarray:
        if isinstance(x, np.ndarray):
            return x.astype(np.int16, copy=False)
        if isinstance(x, Image.Image):
            return np.asarray(x.convert("RGB"), dtype=np.int16)
        return np.asarray(Image.open(x).convert("RGB"), dtype=np.int16)

    cur = _arr(during)
    base = _arr(before) if before is not None else None
    rows: list[tuple[float, float, str]] = []
    for name, box in NUMBER_AREAS_PX.items():
        h, c = _cell_scores(cur, box)
        if base is not None:
            hb, cb = _cell_scores(base, box)
            h = max(0.0, h - hb)
            c = max(0.0, c - cb)
        rows.append((h, c, name))
    hot = frozenset(n for _, _, n in sorted(rows, key=lambda t: -t[0])[:hot_count])
    cold = frozenset(n for _, _, n in sorted(rows, key=lambda t: -t[1])[:cold_count])
    return HotColdSets(hot=hot, cold=cold, source="cloth", captured_at=time.time())


def reveal_and_read_hot_cold(
    *,
    ip: str,
    agent: Any,
    out_dir: Path | str | None = None,
    focus: str = "OneHand",
    settle_ms: int = 350,
) -> HotColdSets:
    """
    Capture baseline → click HOT_COLD → capture during the 5 s flash → read sets.

    Does not wait for the flash to end. Caller should place bets promptly
    (well under `HOT_COLD_DURATION_S`) then SPIN.
    """
    from automation.remote_input_agent import capture_client_screenshot

    out = Path(out_dir) if out_dir else Path("_tmp_logs/slot_roulette/hotcold")
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S")

    def grab(name: str, steps: list[dict]) -> Path:
        dest = out / f"{stamp}_{name}.png"
        ok, msg = capture_client_screenshot(
            ip=ip,
            agent=agent,
            dest=dest,
            focus_process=focus,
            settle_ms=settle_ms,
            steps=steps,
            timeout=120,
        )
        if not ok:
            raise RuntimeError(f"hot/cold capture failed ({name}): {msg}")
        return dest

    focus_step = {"type": "focus_process", "value": focus, "ms": 350}
    before = grab("before", [focus_step])
    during = grab(
        "during",
        [
            focus_step,
            {"type": "click", "value": hot_cold_click_spec(focus), "ms": 400},
        ],
    )
    return read_hot_cold_from_cloth(during, before=before)


__all__ = [
    "COLD_COUNT",
    "HOT_COLD_BUTTON",
    "HOT_COLD_DURATION_S",
    "HOT_COUNT",
    "HotColdSets",
    "hot_cold_click_center_px",
    "hot_cold_click_spec",
    "read_hot_cold_from_cloth",
    "reveal_and_read_hot_cold",
]
