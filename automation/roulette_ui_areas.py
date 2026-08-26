"""
Clickable **areas** (rectangles) for the **layout1** roulette skin.

All boxes are client pixels at 1920x1080 and were measured from a live capture
of cabinet 10.0.0.90 (``_tmp_logs/map_overlay/screen_base.png``) by detecting
the cloth separator lines and button borders.

Board separator lines found on that capture:
  vertical   126, 214, 337, 462, 586, 711, 835, 960, 1084, 1208, 1333, 1457,
             1582, 1706, 1793
  horizontal 274, 362, 487, 613, 738, 826

The module-level tables stay for the many callers that only ever touch layout1;
:data:`LAYOUT1` is the same data as a :class:`~automation.roulette_surface.Surface`
so layout-aware tools can ask for it by id. layout2 lives in
``automation/roulette_layout2_areas.py``.
"""

from __future__ import annotations

from typing import Any

from automation.roulette_surface import (
    Surface,
    area_pct_dict,
    pct_area_to_px,
    register,
)

CLIENT_W = 1920
CLIENT_H = 1080

# --- cloth geometry (px) ---------------------------------------------------
COL_X: tuple[int, ...] = (214, 337, 462, 586, 711, 835, 960, 1084, 1208, 1333, 1457, 1582, 1706)
ROW_Y: tuple[int, ...] = (362, 487, 613, 738)  # top, mid, bot boundaries
ZERO_X = (126, 214)
ZERO_SPLIT_Y = 550
DOZEN_Y = (274, 362)
COLUMN_X = (1706, 1793)  # "2 a 1"
EVEN_Y = (738, 826)

# Row index 0 = top strip (3,6,..36), 1 = middle (2,5,..35), 2 = bottom (1,4,..34)
_ROW_BASE = (3, 2, 1)


def _number_boxes() -> dict[str, tuple[int, int, int, int]]:
    out: dict[str, tuple[int, int, int, int]] = {}
    for col in range(12):
        x0, x1 = COL_X[col], COL_X[col + 1]
        for row in range(3):
            y0, y1 = ROW_Y[row], ROW_Y[row + 1]
            out[str(col * 3 + _ROW_BASE[row])] = (x0, y0, x1, y1)
    out["0"] = (ZERO_X[0], ZERO_SPLIT_Y, ZERO_X[1], ROW_Y[3])
    out["00"] = (ZERO_X[0], ROW_Y[0], ZERO_X[1], ZERO_SPLIT_Y)
    return out


def _outside_boxes() -> dict[str, tuple[int, int, int, int]]:
    dy0, dy1 = DOZEN_Y
    ey0, ey1 = EVEN_Y
    even_edges = (214, 462, 711, 960, 1208, 1457, 1706)
    even_names = ("1-18", "ODD", "RED", "BLACK", "EVEN", "19-36")
    out: dict[str, tuple[int, int, int, int]] = {
        "1-12": (COL_X[0], dy0, COL_X[4], dy1),
        "13-24": (COL_X[4], dy0, COL_X[8], dy1),
        "25-36": (COL_X[8], dy0, COL_X[12], dy1),
        "2to1_top": (COLUMN_X[0], ROW_Y[0], COLUMN_X[1], ROW_Y[1]),
        "2to1_mid": (COLUMN_X[0], ROW_Y[1], COLUMN_X[1], ROW_Y[2]),
        "2to1_bot": (COLUMN_X[0], ROW_Y[2], COLUMN_X[1], ROW_Y[3]),
    }
    for i, name in enumerate(even_names):
        out[name] = (even_edges[i], ey0, even_edges[i + 1], ey1)
    return out


NUMBER_AREAS_PX: dict[str, tuple[int, int, int, int]] = _number_boxes()
OUTSIDE_AREAS_PX: dict[str, tuple[int, int, int, int]] = _outside_boxes()

# --- UI chrome (px, x0, y0, x1, y1) ---------------------------------------
UI_AREAS_PX: dict[str, tuple[int, int, int, int]] = {
    # Top-right panel. GameSelector (cards/dice icon) sits above the lock.
    "LAYOUT_SWITCH": (1454, 7, 1513, 65),
    "LOCK": (1454, 72, 1513, 125),
    "IDIOMA": (1520, 8, 1711, 66),
    "COBRAR": (1716, 8, 1909, 66),
    "AYUDA": (1520, 72, 1711, 126),
    "LLAMAR": (1716, 72, 1909, 126),
    # Top-centre spin / countdown disc
    "START": (905, 30, 1013, 138),
    # Top info bar (panel spans x 11-1414, y 15-118; the START disc overlaps it).
    # CREDIT_DISPLAY is a toggle: tapping it swaps credits <-> currency amounts.
    "CREDIT_DISPLAY": (11, 15, 370, 118),
    "LAST_BET": (371, 15, 641, 118),
    "LAST_WIN": (642, 15, 889, 118),
    # OPACIDAD slider: eye-off icon, track, handle ring, eye-on icon. See
    # UI_SLIDERS_PX for the value <-> handle mapping.
    "OPACITY_SLIDER": (1058, 66, 1272, 113),
    "PLAYER": (1319, 15, 1414, 118),
    # History strip: stats panel opener plus the chevrons at either end.
    "STATISTICS": (13, 156, 99, 200),
    # Chevron glyphs measured at 112-124 / 1792-1804 x 167-197, padded to a
    # clickable size without spilling into STATISTICS or HISTORY_STRIP.
    "HISTORY_PREV": (102, 156, 134, 209),
    "HISTORY_NEXT": (1782, 155, 1814, 208),
    # The last-numbers bar itself. Exact rect from RouletteGui.pck LastNumbersBar:
    # TouchScreenButtonHistory position (957,191), RectangleShape2D extents (811,50).
    "HISTORY_STRIP": (146, 141, 1768, 241),
    # Cloth-side shortcuts
    "APUESTAS_PREDEFINIDAS": (22, 759, 171, 825),
    "CHANGE_VIEW": (1745, 765, 1890, 815),
    "VECINOS": (29, 859, 159, 903),
    "FINALES": (188, 859, 318, 903),
    "COMPLETO": (347, 859, 477, 903),
    "VECINOS_0": (1429, 859, 1559, 903),
    "HUERFANOS": (1588, 859, 1718, 903),
    "VECINOS_00": (1749, 859, 1879, 903),
    # Bottom bar
    "DENOM": (31, 952, 117, 1038),
    "MUESTRA_GANANCIAS": (138, 952, 224, 1038),
    "CALIENTE_FRIO": (246, 954, 332, 1038),
    "PAYTABLE": (354, 954, 492, 1038),
    "BORRADOR": (1422, 952, 1562, 1034),
    "CANCELAR_TODO": (1576, 952, 1716, 1034),
    "REPETIR": (1730, 952, 1870, 1034),
    # Chips (chip_10 is rendered larger while selected)
    "chip_1": (718, 958, 792, 1032),
    "chip_5": (820, 958, 894, 1032),
    "chip_10": (908, 934, 1010, 1036),
    "chip_50": (1022, 958, 1096, 1032),
    "chip_100": (1122, 958, 1200, 1036),
}

# Statistics screen (modal): only present after STATISTICS is pressed. Kept out of
# UI_AREAS_PX because on the game screen these boxes land on the chip tray.
# STATS_EXIT is the only way back — pressing the STATISTICS tab again does nothing.
STATS_PANEL_AREAS_PX: dict[str, tuple[int, int, int, int]] = {
    "STATS_TAB": (0, 139, 150, 296),
    "STATS_BACK": (810, 949, 899, 1040),
    "STATS_EXIT": (920, 949, 1009, 1040),
    "STATS_NEXT": (1028, 949, 1117, 1040),
}

# --- sliders ---------------------------------------------------------------
# Godot builds these as one wide TouchScreenButton running DragScrollButton.cs,
# which reports the picked X through the SelectedXAmount signal. A plain tap on
# the track therefore sets the value outright; a drag works too but is not
# required. Value 0.0 leaves the handle at handle_min_x, 1.0 at handle_max_x.
#
# OPACIDAD limits measured on .90 by over-dragging past both ends: the handle
# ring clamped at x=1103 (tap target 1020) and x=1233 (tap target 1320), and it
# lands within 1 px of any tap in between (tap 1120 -> 1119, tap 1150 -> 1149).
#
# ``ring_*`` bound the search used to read the handle back off a screenshot: the
# ring is the only part of the widget reaching above y=78 (track rows are 85..92,
# the eye icons 78..102), so those rows isolate it.
UI_SLIDERS_PX: dict[str, dict[str, int]] = {
    "OPACITY_SLIDER": {
        "track_y": 89,
        "handle_min_x": 1103,
        "handle_max_x": 1233,
        "ring_x0": 1040,
        "ring_x1": 1300,
        "ring_y0": 62,
        "ring_y1": 78,
        "ring_min_luma": 100,
    },
}

UI_SLIDERS: tuple[str, ...] = tuple(UI_SLIDERS_PX)

# Controls that must never be automated.
UI_FORBIDDEN = ("COBRAR", "LLAMAR", "LOCK")

# Read-only readouts — mapped so the overlay/OCR can find them, never clicked.
UI_INFO_ONLY = ("LAST_BET", "LAST_WIN", "PLAYER")

# The history strip is one big draggable button whose ButtonHeld signal opens the
# statistics modal, so blind clicking it derails a run.
UI_AVOID = ("HISTORY_STRIP",)


LAYOUT1: Surface = register(
    Surface(
        layout_id="layout1",
        skin="futura_doublezero",
        description="Square American double-zero cloth, dozens above the numbers, "
        "spin disc at top centre (Alegro default on .90).",
        client_w=CLIENT_W,
        client_h=CLIENT_H,
        numbers=NUMBER_AREAS_PX,
        outside=OUTSIDE_AREAS_PX,
        ui=UI_AREAS_PX,
        stats=STATS_PANEL_AREAS_PX,
        sliders=UI_SLIDERS_PX,
        forbidden=UI_FORBIDDEN,
        info_only=UI_INFO_ONLY,
        avoid=UI_AVOID,
    )
)

UI_CLICK_AREAS_PCT: dict[str, tuple[float, float, float, float]] = LAYOUT1.ui_areas_pct
STATS_CLICK_AREAS_PCT: dict[str, tuple[float, float, float, float]] = LAYOUT1.stats_areas_pct
BET_CLICK_AREAS_PCT: dict[str, tuple[float, float, float, float]] = LAYOUT1.bet_areas_pct
ALL_CLICK_AREAS_PCT: dict[str, tuple[float, float, float, float]] = LAYOUT1.all_areas_pct


def slider_value_to_px(name: str, value: float) -> tuple[int, int]:
    """Screen point that sets *name* to *value* (0.0 = min, 1.0 = max)."""
    return LAYOUT1.slider_value_to_px(name, value)


def slider_px_to_value(name: str, x: float) -> float:
    """Inverse of :func:`slider_value_to_px` — read a value off a handle x."""
    return LAYOUT1.slider_px_to_value(name, x)


def slider_meta(name: str) -> dict[str, Any] | None:
    """Slider geometry for a hitbox entry (px + pct, both axes)."""
    return LAYOUT1.slider_meta(name)


def build_hitbox_fields(
    button_id: str,
    *,
    client_w: int = CLIENT_W,
    client_h: int = CLIENT_H,
    kind: str | None = None,
    note: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build x/y/width/height + click_center + click_area_pct for any mapped id."""
    return LAYOUT1.build_hitbox_fields(
        button_id,
        client_w=client_w,
        client_h=client_h,
        kind=kind,
        note=note,
        extra=extra,
    )


# Back-compat alias used by the layout1 full mapper.
build_ui_hitbox_fields = build_hitbox_fields


def apply_areas_to_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Merge measured area fields into an existing hitbox entry when defined."""
    fields = build_hitbox_fields(str(entry.get("id") or ""))
    if fields is None:
        return entry
    merged = dict(entry)
    merged.update(fields)
    return merged


apply_ui_areas_to_entry = apply_areas_to_entry

__all__ = [
    "ALL_CLICK_AREAS_PCT",
    "BET_CLICK_AREAS_PCT",
    "CLIENT_H",
    "CLIENT_W",
    "LAYOUT1",
    "NUMBER_AREAS_PX",
    "OUTSIDE_AREAS_PX",
    "STATS_CLICK_AREAS_PCT",
    "STATS_PANEL_AREAS_PX",
    "UI_AREAS_PX",
    "UI_AVOID",
    "UI_CLICK_AREAS_PCT",
    "UI_FORBIDDEN",
    "UI_INFO_ONLY",
    "UI_SLIDERS",
    "UI_SLIDERS_PX",
    "apply_areas_to_entry",
    "apply_ui_areas_to_entry",
    "area_pct_dict",
    "build_hitbox_fields",
    "build_ui_hitbox_fields",
    "pct_area_to_px",
    "slider_meta",
    "slider_px_to_value",
    "slider_value_to_px",
]
