"""
Clickable **areas** for OneHand **Slot Roulette** (theme ``RouletteGame``).

This is **not** a Godot ruleta skin. On lab cabinet ``10.0.0.90`` the game runs
as ``OneHand.exe`` with ``Themes\\RouletteGame\\config_SetClear_3Screens.xml``.
The client is a **triple-stack** 1920×3240 window (three 1920×1080 panels):

* top (y 0..1080) — GOLDCLUB ROULETTE marquee
* mid (y 1080..2160) — wheel + hot/cold + statistics (mostly display)
* bot (y 2160..3240) — betting cloth, chips, SPIN, chrome  ← interactive map
  HOT_COLD pill flashes 5 hot + 5 cold cell highlights for ~5 seconds
  (see automation.roulette_slot_hotcold for bot read-back).

European single-zero cloth (no ``00``). Geometry measured from a live OneHand
client capture (``_tmp_logs/slot_roulette/play_client.png`` / panel_bot_cloth)
via red/black cell colour runs and separator edges — see
``_tmp_logs/slot_roulette/measure_annotated.png``.

Panel-local coordinates below are offset by ``PANEL_Y`` into full-client space.
"""

from __future__ import annotations

from typing import Any

from automation.roulette_surface import Surface, register

CLIENT_W = 1920
CLIENT_H = 3240
PANEL_H = 1080
PANEL_Y = 2 * PANEL_H  # bottom (cloth) panel origin in full-client Y

# --- cloth geometry in bottom-panel pixels, then lifted to full client ------
# Measured 2026-07-29 on .90 from panel_bot_cloth.png colour runs.
_COL_X_P: tuple[int, ...] = (
    215, 341, 468, 595, 722, 849, 976, 1103, 1230, 1357, 1484, 1611, 1736
)
_ROW_Y_P: tuple[int, ...] = (156, 283, 411, 538)
_ZERO_X_P = (68, 215)
_COLUMN_X_P = (1736, 1827)  # "2 to 1"
_DOZEN_Y_P = (538, 628)
_EVEN_Y_P = (628, 714)

COL_X: tuple[int, ...] = _COL_X_P
ROW_Y: tuple[int, ...] = tuple(y + PANEL_Y for y in _ROW_Y_P)
ZERO_X: tuple[int, ...] = _ZERO_X_P
COLUMN_X: tuple[int, ...] = _COLUMN_X_P
DOZEN_Y: tuple[int, ...] = tuple(y + PANEL_Y for y in _DOZEN_Y_P)
EVEN_Y: tuple[int, ...] = tuple(y + PANEL_Y for y in _EVEN_Y_P)

_ROW_BASE = (3, 2, 1)  # top strip 3,6,..; mid 2,5,..; bot 1,4,..


def _py(y: int) -> int:
    return y + PANEL_Y


def _box_p(x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    return (x0, _py(y0), x1, _py(y1))


def _number_boxes() -> dict[str, tuple[int, int, int, int]]:
    out: dict[str, tuple[int, int, int, int]] = {}
    for col in range(12):
        x0, x1 = COL_X[col], COL_X[col + 1]
        for row in range(3):
            y0, y1 = _ROW_Y_P[row], _ROW_Y_P[row + 1]
            out[str(col * 3 + _ROW_BASE[row])] = _box_p(x0, y0, x1, y1)
    # European: single full-height zero wedge (no 00 split).
    out["0"] = _box_p(ZERO_X[0], _ROW_Y_P[0], ZERO_X[1], _ROW_Y_P[3])
    return out


def _outside_boxes() -> dict[str, tuple[int, int, int, int]]:
    dy0, dy1 = _DOZEN_Y_P
    ey0, ey1 = _EVEN_Y_P
    even_edges = (215, 468, 722, 976, 1229, 1482, 1736)
    even_names = ("1-18", "EVEN", "RED", "BLACK", "ODD", "19-36")
    out: dict[str, tuple[int, int, int, int]] = {
        "1-12": _box_p(COL_X[0], dy0, COL_X[4], dy1),
        "13-24": _box_p(COL_X[4], dy0, COL_X[8], dy1),
        "25-36": _box_p(COL_X[8], dy0, COL_X[12], dy1),
        "2to1_top": _box_p(COLUMN_X[0], _ROW_Y_P[0], COLUMN_X[1], _ROW_Y_P[1]),
        "2to1_mid": _box_p(COLUMN_X[0], _ROW_Y_P[1], COLUMN_X[1], _ROW_Y_P[2]),
        "2to1_bot": _box_p(COLUMN_X[0], _ROW_Y_P[2], COLUMN_X[1], _ROW_Y_P[3]),
    }
    for i, name in enumerate(even_names):
        out[name] = _box_p(even_edges[i], ey0, even_edges[i + 1], ey1)
    return out


NUMBER_AREAS_PX: dict[str, tuple[int, int, int, int]] = _number_boxes()
OUTSIDE_AREAS_PX: dict[str, tuple[int, int, int, int]] = _outside_boxes()

# --- UI chrome (full-client px) --------------------------------------------
# Chip centres measured on panel_bot_cloth (y ~860); radius ~40.
_CHIP_R = 40
_CHIP_CENTERS_P = {
    "chip_20": (730, 862),
    "chip_50": (821, 867),
    "chip_100": (926, 863),
    "chip_200": (1046, 855),
    "chip_500": (1170, 862),
}


def _chip_box(cx: int, cy: int) -> tuple[int, int, int, int]:
    return _box_p(cx - _CHIP_R, cy - _CHIP_R, cx + _CHIP_R, cy + _CHIP_R)


UI_AREAS_PX: dict[str, tuple[int, int, int, int]] = {
    # History strip + clock button (top of cloth panel).
    "HISTORY_BTN": _box_p(40, 37, 100, 110),
    "HISTORY_STRIP": _box_p(110, 30, 1850, 130),
    # Left of dozens/even row (not beside the zero tip — that overlapped "0").
    # Measured 2026-07-29 on panel_bot_cloth: eye button, then one pill with
    # flame|snowflake side-by-side (Spanish: MOSTRAR PREMIOS).
    "SHOW_WINS": _box_p(20, 630, 120, 710),
    # One pill: flame|snowflake. Reveals 5 hot + 5 cold cloth highlights for ~5 s.
    "HOT_COLD": _box_p(20, 715, 120, 785),
    # Right of 2to1, level with the call-bet strip (Spanish: CAMBIAR VISTA).
    "CHANGE_VIEW": _box_p(1805, 720, 1910, 800),  # toggles racetrack oval around cloth
    # Call / special bets (bottom-left). ES: VECINOS / FINAL / COMPLETO / …
    "NEIGHBORS": _box_p(40, 820, 230, 880),
    "FINAL": _box_p(240, 820, 430, 880),
    "COMPLETE": _box_p(440, 820, 630, 880),
    "ORPHELINS": _box_p(40, 900, 185, 970),
    "SERIES_5_8": _box_p(195, 900, 340, 970),
    "NEIGHBORS_OF_ZERO": _box_p(350, 900, 510, 970),
    "ZERO_GAME": _box_p(520, 900, 640, 970),
    # Chips.
    **{name: _chip_box(cx, cy) for name, (cx, cy) in _CHIP_CENTERS_P.items()},
    # Bet edit actions (right of chips). ES: BORRADOR / BORRAR TODO / DOBLE (x2).
    "ERASER": _box_p(1280, 820, 1385, 930),
    "CANCEL_ALL": _box_p(1395, 820, 1500, 930),
    "DOUBLE": _box_p(1510, 820, 1615, 930),
    # Footer bar. ES: AYUDA / MÁS JUEGOS / SALDO / PREMIO / JUGAR.
    "VOLUME": _box_p(40, 990, 100, 1055),
    "HELP": _box_p(105, 990, 250, 1055),
    "MORE_GAMES": _box_p(260, 990, 450, 1055),
    "BALANCE_DISPLAY": _box_p(520, 990, 820, 1055),
    "WIN_DISPLAY": _box_p(840, 990, 1050, 1055),
    "DENOM_DISPLAY": _box_p(1070, 990, 1280, 1055),
    "BET_DISPLAY": _box_p(1300, 990, 1520, 1055),
    "SPIN": _box_p(1647, 978, 1895, 1055),  # mandatory to play; empty board SPIN = rebet
    "EDGE_TAB": _box_p(1895, 990, 1918, 1055),
}

# Mid-panel (wheel / stats) — display-only hit regions for overlays / info.
STATS_PANEL_AREAS_PX: dict[str, tuple[int, int, int, int]] = {
    "WHEEL": (520, 1080 + 200, 1400, 1080 + 900),
    "HOT_NUMBERS": (40, 1080 + 40, 520, 1080 + 160),
    "COLD_NUMBERS": (1400, 1080 + 40, 1880, 1080 + 160),
    "LAST_NUMBER": (1500, 1080 + 350, 1850, 1080 + 650),
}

UI_OVERLAYS: dict[str, dict[str, tuple[int, int, int, int]]] = {
    # HELP opens a multi-page book over the cloth (captured 2026-07-29 help_open.png).
    # Nav is one gold-bordered trapezoid: << | BACK TO GAME | >>.
    "help": {
        "HELP_PANEL": _box_p(300, 60, 1620, 890),
        "HELP_PREV": _box_p(720, 900, 820, 975),
        "HELP_BACK": _box_p(820, 900, 1100, 975),
        "HELP_NEXT": _box_p(1100, 900, 1200, 975),
    },
    "history": {
        "HISTORY_PANEL": _box_p(160, 80, 1760, 980),
        # Measured 2026-07-29 on live HISTORY: CLOSE centre ~panel (961,830).
        "HISTORY_CLOSE": _box_p(880, 790, 1040, 870),
        "HISTORY_TITLE": _box_p(700, 100, 1220, 180),
    },
    # CHANGE_VIEW toggles a wheel-order oval around the square cloth (no separate mini-cloth).
    "racetrack": {
        "race_0": (937, 2315, 969, 2347),
        "race_1": (411, 2794, 443, 2826),
        "race_10": (1001, 2871, 1033, 2903),
        "race_11": (1462, 2794, 1494, 2826),
        "race_12": (459, 2377, 491, 2409),
        "race_13": (1615, 2719, 1647, 2751),
        "race_14": (258, 2719, 290, 2751),
        "race_15": (1189, 2331, 1221, 2363),
        "race_16": (624, 2847, 656, 2879),
        "race_17": (1679, 2535, 1711, 2567),
        "race_18": (194, 2535, 226, 2567),
        "race_19": (1307, 2351, 1339, 2383),
        "race_2": (1583, 2448, 1615, 2480),
        "race_20": (326, 2759, 358, 2791),
        "race_21": (1507, 2410, 1539, 2442),
        "race_22": (178, 2582, 210, 2614),
        "race_23": (1128, 2863, 1160, 2895),
        "race_24": (745, 2863, 777, 2895),
        "race_25": (1641, 2490, 1673, 2522),
        "race_26": (808, 2319, 840, 2351),
        "race_27": (1663, 2675, 1695, 2707),
        "race_28": (366, 2410, 398, 2442),
        "race_29": (232, 2490, 264, 2522),
        "race_3": (684, 2331, 716, 2363),
        "race_30": (1362, 2824, 1394, 2856),
        "race_31": (210, 2675, 242, 2707),
        "race_32": (1065, 2319, 1097, 2351),
        "race_33": (511, 2824, 543, 2856),
        "race_34": (1695, 2582, 1727, 2614),
        "race_35": (566, 2351, 598, 2383),
        "race_36": (1547, 2759, 1579, 2791),
        "race_4": (1414, 2377, 1446, 2409),
        "race_5": (872, 2871, 904, 2903),
        "race_6": (1690, 2629, 1722, 2661),
        "race_7": (290, 2448, 322, 2480),
        "race_8": (1249, 2847, 1281, 2879),
        "race_9": (183, 2629, 215, 2661),
        "RACE_NEIGHBORS_OF_ZERO": (733, 2309, 1173, 2436),
        "RACE_ZERO_GAME": (793, 2388, 1113, 2483),
        "RACE_ORPHANS_LEFT": (107, 2490, 348, 2730),
        "RACE_ORPHANS_RIGHT": (1557, 2490, 1798, 2730),
        "RACE_SERIES_5_8": (673, 2752, 1233, 2910),
    },
}

UI_FORBIDDEN = ("MORE_GAMES",)  # exits RouletteGame back to multigamer
UI_INFO_ONLY = (
    "HISTORY_STRIP",
    "BALANCE_DISPLAY",
    "WIN_DISPLAY",
    "DENOM_DISPLAY",
    "BET_DISPLAY",
    "WHEEL",
    "HOT_NUMBERS",
    "COLD_NUMBERS",
    "LAST_NUMBER",
    "HELP_PANEL",
    "HISTORY_PANEL",
    "HISTORY_TITLE",
)
UI_AVOID = ("EDGE_TAB",)
UI_UNPROVEN = (
    "SHOW_WINS",
    "HOT_COLD",
    "CHANGE_VIEW",
    "NEIGHBORS",
    "FINAL",
    "COMPLETE",
    "ORPHELINS",
    "SERIES_5_8",
    "NEIGHBORS_OF_ZERO",
    "ZERO_GAME",
    "ERASER",
    "CANCEL_ALL",
    "DOUBLE",
    "VOLUME",
    "HELP",
    "HISTORY_BTN",
    "EDGE_TAB",
)

LAYOUT_SLOT: Surface = register(
    Surface(
        layout_id="slot",
        skin="RouletteGame",
        description="OneHand Slot Roulette (European single-zero), triple-stack "
        "1920x3240 client; interactive cloth on the bottom panel.",
        client_w=CLIENT_W,
        client_h=CLIENT_H,
        numbers=NUMBER_AREAS_PX,
        outside=OUTSIDE_AREAS_PX,
        ui=UI_AREAS_PX,
        stats=STATS_PANEL_AREAS_PX,
        overlays=UI_OVERLAYS,
        forbidden=UI_FORBIDDEN,
        info_only=UI_INFO_ONLY,
        avoid=UI_AVOID,
        unproven=UI_UNPROVEN,
    )
)


def surface_dict() -> dict[str, Any]:
    return {
        "layout_id": LAYOUT_SLOT.layout_id,
        "skin": LAYOUT_SLOT.skin,
        "client": {"width": CLIENT_W, "height": CLIENT_H},
        "panel_y": PANEL_Y,
        "numbers": len(NUMBER_AREAS_PX),
        "outside": len(OUTSIDE_AREAS_PX),
        "ui": len(UI_AREAS_PX),
    }


__all__ = [
    "CLIENT_H",
    "CLIENT_W",
    "COL_X",
    "COLUMN_X",
    "DOZEN_Y",
    "EVEN_Y",
    "LAYOUT_SLOT",
    "NUMBER_AREAS_PX",
    "OUTSIDE_AREAS_PX",
    "PANEL_Y",
    "ROW_Y",
    "UI_AREAS_PX",
    "ZERO_X",
    "surface_dict",
]
