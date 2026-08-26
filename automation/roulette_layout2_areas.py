"""
Clickable **areas** for the **layout2** roulette skin (``futura_doublezeroCrycle``).

Selected from layout1 through the top-right game selector. It draws the same
American double-zero game but rearranges nearly everything, so none of the
layout1 boxes transfer:

* dozens sit **below** the numbers (layout1 puts them above), evens below those
* ``START`` is a bottom-right button, not a countdown disc at top centre
* the opacity slider moved under ``CRÉDITO`` in the top-left readout block
* the top-right icon strip is ``PAÑO IDIOMA MENÚ AYUDA COBRAR`` — no lock, no
  "llamar al asistente"
* the last-numbers strip is a plain capsule with no paging chevrons
* new rails flank the cloth: ``SERIES EXTRA`` (left) and ``SERIES`` (right),
  each captioned between two eye glyphs, and ``CAMBIAR VISTA`` moved to the
  right rail

Four sub-screens hide behind the chrome — the statistics panel, the language
chooser, the help book and the MENÚ row — and each has its own way out. A fifth
entry, ``racetrack``, is not a panel but the other *view* of the game: the oval's
38 pockets plus a complete small cloth drawn inside it. See ``UI_OVERLAYS``.

All boxes are client pixels at 1920x1080, measured from a live capture of
cabinet 10.0.0.90 (``_tmp_logs/layout2/screen_base.png``) by detecting the cloth
separator lines and button fills — see ``_tmp_logs/layout2/measure_*.py``.

Cloth separator lines found on that capture:
  vertical   124, 244, 363, 483, 602, 721, 841, 960, 1080, 1199, 1319, 1438,
             1558, 1678, 1799
  horizontal 324, 443, 564, 684, 803, 922  (zero split 504)
"""

from __future__ import annotations

from typing import Any

from automation.roulette_surface import Surface, register

CLIENT_W = 1920
CLIENT_H = 1080

# --- cloth geometry (px) ---------------------------------------------------
COL_X: tuple[int, ...] = (244, 363, 483, 602, 721, 841, 960, 1080, 1199, 1319, 1438, 1558, 1678)
ROW_Y: tuple[int, ...] = (324, 443, 564, 684)  # top, mid, bot boundaries
ZERO_X = (124, 244)
ZERO_SPLIT_Y = 504
# layout2 inverts layout1: dozens under the grid, even-money row under those.
DOZEN_Y = (684, 803)
EVEN_Y = (803, 922)
COLUMN_X = (1678, 1799)  # "2 a 1" (rotated captions)

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
    even_edges = (244, 483, 721, 960, 1199, 1438, 1678)
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
    # Top-left readout block. DENOMINACIÓN is a bordered white panel here, not a
    # bottom-bar button as in layout1.
    "DENOM": (51, 8, 281, 121),
    # CRÉDITO caption + value only: the slider directly underneath is a separate
    # control, so the toggle box must stop above it.
    "CREDIT_DISPLAY": (330, 6, 560, 84),
    # OPACIDAD: eye-off icon, track, handle, eye-on icon (see UI_SLIDERS_PX).
    "OPACITY_SLIDER": (333, 86, 584, 122),
    "LAST_BET": (655, 6, 935, 124),
    "LAST_WIN": (985, 6, 1255, 124),
    # Top-right icon strip.
    "PANO": (1307, 20, 1395, 108),
    "IDIOMA": (1425, 20, 1513, 109),
    "MENU": (1543, 19, 1631, 109),
    "AYUDA": (1661, 19, 1749, 109),
    "COBRAR": (1779, 20, 1867, 109),
    # Last-numbers capsule and the JUGADOR badge left of it. The capsule's black
    # interior measures x 166..1749 / y 163..219; the box adds the rounded ends
    # while stopping short of the badge and the STATISTICS caption above it.
    "STATISTICS": (795, 132, 1018, 159),
    # The badge is a button: it opens the statistics panel (STATS_* below).
    "PLAYER": (28, 145, 124, 240),
    "HISTORY_STRIP": (140, 160, 1770, 222),
    # Right rail, above SERIES: two curved arrows + "CAMBIAR VISTA" caption.
    "CHANGE_VIEW": (1820, 313, 1894, 430),
    # Each rail is captioned between an open eye and a crossed eye, which reads as
    # a show/hide pair but is not one: on .90 either glyph simply *toggles* that
    # rail's pills, and tapping the same glyph twice turns them on and then off
    # again. The names are therefore positional, not functional.
    "SERIES_EXTRA_EYE_TOP": (34, 580, 89, 620),
    "SERIES_EXTRA_EYE_BOTTOM": (34, 656, 89, 696),
    "SERIES_EYE_TOP": (1833, 580, 1888, 620),
    "SERIES_EYE_BOTTOM": (1834, 656, 1889, 696),
    # Cloth-side pills. They exist only while their rail is toggled on, and a
    # click on the bare cloth where a hidden pill would be does nothing at all.
    "VECINOS": (19, 718, 218, 762),
    "FINALES": (19, 773, 218, 817),
    "COMPLETO": (19, 828, 218, 872),
    "VECINOS_0": (1700, 719, 1899, 763),
    "HUERFANOS": (1700, 774, 1899, 818),
    "VECINOS_00": (1700, 829, 1899, 874),
    # Bottom bar. PAYTABLE is the green "STANDARD 36X" tile.
    "PAYTABLE": (24, 950, 171, 1062),
    "CANCELAR_TODO": (204, 950, 323, 1062),
    "BORRADOR": (356, 950, 475, 1062),
    "MUESTRA_GANANCIAS": (508, 950, 597, 1062),
    # Chips (the selected one is drawn larger — chip_1 here).
    "chip_1": (663, 959, 777, 1073),
    "chip_5": (792, 975, 889, 1073),
    "chip_10": (912, 976, 1009, 1073),
    "chip_50": (1032, 976, 1129, 1073),
    "chip_100": (1151, 975, 1249, 1073),
    "REPETIR": (1597, 950, 1716, 1062),
    # START is a bottom-right button in this skin, not the top-centre disc.
    "START": (1749, 950, 1868, 1062),
    # White tab clipped by the right screen edge, marked with a "<->" glyph.
    "EDGE_TAB": (1895, 977, 1919, 1029),
}

# --- statistics panel (full-screen, over the cloth) ------------------------
# Reached by tapping the JUGADOR badge, not by the STATISTICS caption above the
# strip and not by holding the strip the way layout1 does — both of those were
# tried on .90 and do nothing. Measured from the panel itself
# (_tmp_logs/verify_ui/layout2/shots/LAST_BET_after.png).
#
# SALIR is the only way out: Escape and a second tap on the badge both leave the
# panel up, so any automation that opens it must click STATS_EXIT to get back.
STATS_PANEL_AREAS_PX: dict[str, tuple[int, int, int, int]] = {
    "STATS_TITLE": (695, 132, 1226, 205),
    "STATS_LAST_NUMBERS": (140, 240, 1782, 745),
    "STATS_HOT": (140, 790, 922, 922),
    "STATS_COLD": (984, 790, 1812, 922),
    "STATS_EXIT": (844, 960, 1076, 1074),
}

# --- other sub-screens ------------------------------------------------------
# Screens that only exist after a click, keyed by the name of the screen. The
# statistics panel above is folded in by Surface as "statistics".
#
# LANGUAGE is what IDIOMA opens: a two-row chooser over the cloth. Picking a row
# closes it, and picking the row that is already active is the safe way out —
# there is no cancel button and Escape does nothing.
#
# HELP is what AYUDA opens: a full-screen "INFORMACIÓN DEL JUEGO" book with a
# legend for every control, paged with ATRÁS / SIGUIENTE and closed only by
# SALIR. Escape and a second tap on AYUDA both leave it up.
#
# MENU is what MENÚ opens: the whole game dims and four slabs are laid across the
# cloth. A second tap on MENÚ closes it.
UI_OVERLAYS: dict[str, dict[str, tuple[int, int, int, int]]] = {
    "language": {
        "LANG_TITLE": (715, 322, 1214, 414),
        "LANG_ES": (715, 415, 1214, 505),
        "LANG_EN": (715, 506, 1214, 596),
    },
    "help": {
        "HELP_TITLE": (724, 154, 1211, 198),
        "HELP_INTRO": (24, 212, 1896, 348),
        "HELP_LEGEND": (24, 400, 1896, 946),
        "HELP_BACK": (36, 959, 269, 1073),
        "HELP_EXIT": (843, 959, 1076, 1073),
        "HELP_NEXT": (1650, 959, 1883, 1073),
    },
    "menu": {
        "MENU_ASSISTANT": (248, 810, 484, 925),
        "MENU_IDIOMA": (487, 810, 723, 925),
        "MENU_AYUDA": (726, 810, 962, 925),
        "MENU_PIN_LOCK": (965, 810, 1201, 925),
    },
}

# --- racetrack view ---------------------------------------------------------
# CAMBIAR VISTA swaps the square cloth for an oval of the 38 wheel pockets in
# wheel order, with a shrunken cloth in the middle. It is a second betting
# surface, not decoration: a pocket takes a straight-up bet like its cloth cell.
#
# Measured from a live capture of that view by walling off every patch the oval's
# white lines enclose and keeping the ones painted like a pocket
# (``_tmp_logs/layout2/measure_race.py``). The pockets at the ends of the oval
# are slanted quadrilaterals whose bounding boxes overlap their neighbours', so
# each box below is instead the largest square that fits *inside* its pocket —
# every pixel of it is a safe click.
#
# The order is the American wheel, clockwise from 0 at the top; the two green
# pockets are what pins it to the capture.
RACETRACK_AREAS_PX: dict[str, tuple[int, int, int, int]] = {
    "race_0": (914, 330, 1004, 420),
    "race_28": (1026, 330, 1116, 420),
    "race_9": (1138, 330, 1228, 420),
    "race_26": (1250, 330, 1340, 420),
    "race_30": (1363, 332, 1453, 422),
    "race_11": (1474, 334, 1564, 424),
    "race_7": (1590, 340, 1670, 420),
    "race_20": (1694, 385, 1758, 449),
    "race_32": (1762, 453, 1826, 517),
    "race_17": (1797, 539, 1865, 607),
    "race_5": (1795, 635, 1865, 705),
    "race_22": (1774, 742, 1806, 774),
    "race_34": (1694, 795, 1758, 859),
    "race_15": (1593, 816, 1667, 890),
    "race_3": (1474, 807, 1564, 897),
    "race_24": (1362, 807, 1452, 897),
    "race_36": (1251, 807, 1341, 897),
    "race_13": (1138, 807, 1228, 897),
    "race_1": (1026, 807, 1116, 897),
    "race_00": (914, 807, 1004, 897),
    "race_27": (802, 807, 892, 897),
    "race_10": (690, 807, 780, 897),
    "race_25": (578, 807, 668, 897),
    "race_29": (466, 807, 556, 897),
    "race_12": (354, 807, 444, 897),
    "race_8": (251, 817, 323, 889),
    "race_19": (160, 794, 224, 858),
    "race_31": (92, 727, 156, 791),
    "race_18": (53, 637, 121, 705),
    "race_6": (53, 539, 121, 607),
    "race_21": (91, 453, 155, 517),
    "race_33": (159, 384, 225, 450),
    "race_16": (248, 340, 328, 420),
    "race_4": (354, 334, 444, 424),
    "race_23": (464, 332, 554, 422),
    "race_35": (578, 330, 668, 420),
    "race_14": (690, 330, 780, 420),
    "race_2": (802, 330, 892, 420),
}

# The oval is not the whole view: a complete second cloth is drawn inside it,
# with its own zero column, 3x12 grid, "2 a 1" column, dozens and even-money row.
# Same bets as the big cloth, different pixels, so they need their own ids —
# `mini_` + the id the main cloth uses (`_tmp_logs/layout2/measure_mini.py`).
MINI_COL_X: tuple[int, ...] = (
    600, 659, 719, 778, 838, 897, 957, 1016, 1076, 1135, 1194, 1254, 1314
)
MINI_ROW_Y: tuple[int, ...] = (476, 535, 595, 655)
MINI_ZERO_X = (540, 600)
MINI_ZERO_SPLIT_Y = 565
MINI_DOZEN_Y = (655, 715)
MINI_EVEN_Y = (715, 774)
# The "2 a 1" column has no drawn right border on this cloth; 1373 is where its
# fill ends, one number-column width past the grid.
MINI_COLUMN_X = (1314, 1373)


def _mini_boxes() -> dict[str, tuple[int, int, int, int]]:
    out: dict[str, tuple[int, int, int, int]] = {}
    for col in range(12):
        x0, x1 = MINI_COL_X[col], MINI_COL_X[col + 1]
        for row in range(3):
            y0, y1 = MINI_ROW_Y[row], MINI_ROW_Y[row + 1]
            out[f"mini_{col * 3 + _ROW_BASE[row]}"] = (x0, y0, x1, y1)
    out["mini_0"] = (MINI_ZERO_X[0], MINI_ZERO_SPLIT_Y, MINI_ZERO_X[1], MINI_ROW_Y[3])
    out["mini_00"] = (MINI_ZERO_X[0], MINI_ROW_Y[0], MINI_ZERO_X[1], MINI_ZERO_SPLIT_Y)
    dy0, dy1 = MINI_DOZEN_Y
    ey0, ey1 = MINI_EVEN_Y
    out["mini_1-12"] = (MINI_COL_X[0], dy0, MINI_COL_X[4], dy1)
    out["mini_13-24"] = (MINI_COL_X[4], dy0, MINI_COL_X[8], dy1)
    out["mini_25-36"] = (MINI_COL_X[8], dy0, MINI_COL_X[12], dy1)
    for i, name in enumerate(("1-18", "ODD", "RED", "BLACK", "EVEN", "19-36")):
        out[f"mini_{name}"] = (MINI_COL_X[i * 2], ey0, MINI_COL_X[i * 2 + 2], ey1)
    for i, name in enumerate(("2to1_top", "2to1_mid", "2to1_bot")):
        out[f"mini_{name}"] = (
            MINI_COLUMN_X[0], MINI_ROW_Y[i], MINI_COLUMN_X[1], MINI_ROW_Y[i + 1]
        )
    return out


MINI_BOARD_AREAS_PX: dict[str, tuple[int, int, int, int]] = _mini_boxes()

UI_OVERLAYS["racetrack"] = {**RACETRACK_AREAS_PX, **MINI_BOARD_AREAS_PX}

# --- sliders ---------------------------------------------------------------
# Travel calibrated live on .90 (2026-07-25) by tapping the track at known x and
# reading the ring back: taps from 404 to 519 park the ring at tap-0.5 px, taps
# at/below 396 clamp it to 397.5 and taps at/above 540 clamp it to 519.5. So the
# usable tap span is 398..520 and a value read at either end is within a pixel.
#
# No row belongs to the ring alone on this skin (ring y 90..115, eye icons
# 94..116), but the eyes sit outside x 380..538, so the read window is bounded
# horizontally instead and the ring is picked out by brightness — the ring is
# ~(168,175,167) against a ~(63,68,64) track.
UI_SLIDERS_PX: dict[str, dict[str, int]] = {
    "OPACITY_SLIDER": {
        "track_y": 103,
        "handle_min_x": 398,
        "handle_max_x": 520,
        "ring_x0": 380,
        "ring_x1": 538,
        "ring_y0": 88,
        "ring_y1": 118,
        "ring_min_luma": 120,
    },
}

UI_SLIDERS: tuple[str, ...] = tuple(UI_SLIDERS_PX)

# Cash-out is never automated, and neither is PIN LOCK on the MENÚ overlay: it
# locks the cabinet behind a PIN nobody here has.
UI_FORBIDDEN = ("COBRAR", "MENU_PIN_LOCK")

# STATISTICS and HISTORY_STRIP are here because clicking them was tried and does
# nothing on this skin, not merely because they look like displays. PLAYER is not
# in this list: the badge opens the statistics panel.
#
# PAYTABLE joined them the same way: it is drawn as a button and greys out while
# bets are shut, but tapping it inside an open window on .90 changes nothing —
# this cabinet ships a single paytable, so its chooser has nothing to offer.
# DENOM is *not* here: it looks equally dead for a second and then cycles.
UI_INFO_ONLY = (
    "LAST_BET", "LAST_WIN", "STATISTICS", "HISTORY_STRIP", "PAYTABLE",
    "HELP_TITLE", "HELP_INTRO", "HELP_LEGEND",
)

UI_AVOID = ("EDGE_TAB",)

# Measured, but what they do is not proven live yet.
UI_UNPROVEN = ("EDGE_TAB", "MENU_PIN_LOCK")

LAYOUT2: Surface = register(
    Surface(
        layout_id="layout2",
        skin="futura_doublezeroCrycle",
        description="Crycle American double-zero cloth: dozens below the numbers, "
        "START bottom-right, opacity slider under CRÉDITO, SERIES rails.",
        client_w=CLIENT_W,
        client_h=CLIENT_H,
        numbers=NUMBER_AREAS_PX,
        outside=OUTSIDE_AREAS_PX,
        ui=UI_AREAS_PX,
        stats=STATS_PANEL_AREAS_PX,
        overlays=UI_OVERLAYS,
        sliders=UI_SLIDERS_PX,
        forbidden=UI_FORBIDDEN,
        info_only=UI_INFO_ONLY,
        avoid=UI_AVOID,
        unproven=UI_UNPROVEN,
    )
)

UI_CLICK_AREAS_PCT: dict[str, tuple[float, float, float, float]] = LAYOUT2.ui_areas_pct
BET_CLICK_AREAS_PCT: dict[str, tuple[float, float, float, float]] = LAYOUT2.bet_areas_pct
ALL_CLICK_AREAS_PCT: dict[str, tuple[float, float, float, float]] = LAYOUT2.all_areas_pct


def build_hitbox_fields(
    button_id: str,
    *,
    client_w: int = CLIENT_W,
    client_h: int = CLIENT_H,
    kind: str | None = None,
    note: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return LAYOUT2.build_hitbox_fields(
        button_id,
        client_w=client_w,
        client_h=client_h,
        kind=kind,
        note=note,
        extra=extra,
    )


def slider_value_to_px(name: str, value: float) -> tuple[int, int]:
    return LAYOUT2.slider_value_to_px(name, value)


def slider_px_to_value(name: str, x: float) -> float:
    return LAYOUT2.slider_px_to_value(name, x)


__all__ = [
    "ALL_CLICK_AREAS_PCT",
    "BET_CLICK_AREAS_PCT",
    "CLIENT_H",
    "CLIENT_W",
    "COLUMN_X",
    "COL_X",
    "DOZEN_Y",
    "EVEN_Y",
    "LAYOUT2",
    "MINI_BOARD_AREAS_PX",
    "MINI_COL_X",
    "MINI_ROW_Y",
    "NUMBER_AREAS_PX",
    "RACETRACK_AREAS_PX",
    "OUTSIDE_AREAS_PX",
    "ROW_Y",
    "STATS_PANEL_AREAS_PX",
    "UI_AREAS_PX",
    "UI_AVOID",
    "UI_CLICK_AREAS_PCT",
    "UI_FORBIDDEN",
    "UI_INFO_ONLY",
    "UI_OVERLAYS",
    "UI_SLIDERS",
    "UI_SLIDERS_PX",
    "UI_UNPROVEN",
    "ZERO_SPLIT_Y",
    "ZERO_X",
    "build_hitbox_fields",
    "slider_px_to_value",
    "slider_value_to_px",
]
