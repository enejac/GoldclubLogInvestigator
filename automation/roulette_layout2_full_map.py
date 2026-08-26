"""
Build the full **layout2** (``futura_doublezeroCrycle``) hitbox registry.

Unlike the layout1 mapper, which discovered geometry by clicking a percentage
grid one target per betting window, layout2's boxes come from detected cloth
separator lines (:mod:`automation.roulette_layout2_areas`), so the registry can
be written offline and exactly. Live proof is a separate step:

    python -m automation.roulette_layout2_full_map            # write registry
    python -m automation.roulette_verify_bets --layout layout2 --write-hitboxes

Inside-bet anchors (splits / streets / corners / six-lines) are placed on the
real shared cell edges rather than guessed from percentages, and stay
``anchor_unverified`` until the middleware confirms the coverage:

    python -m automation.roulette_verify_bets --layout layout2 --anchors

All 107 of them are proven on .90, which is also what corrected the zero column
(one three-number bet along cell 2's edge, basket on the outer bottom corner).

Registry out: ``automation/layouts/layout2_hitboxes.json``
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from automation.roulette_bet_catalog import place_bet_for_button
from automation.roulette_layout2_areas import (
    COLUMN_X,
    COL_X,
    DOZEN_Y,
    EVEN_Y,
    LAYOUT2,
    ROW_Y,
    ZERO_SPLIT_Y,
    ZERO_X,
)
from automation.roulette_surface import area_pct_dict

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "automation" / "layouts" / "layout2_hitboxes.json"
CLIENT = {"width": LAYOUT2.client_w, "height": LAYOUT2.client_h}

# Straight-bet ids on the cloth, in reading order used by the verifier.
_ZEROS = ("00", "0")

# Behaviour notes worth carrying into the registry so a later reader does not
# have to rediscover them.
NOTES: dict[str, str] = {
    "DENOM": "Top-left DENOMINACIÓN panel (layout1 has this as a bottom-bar button). "
    "Tapping it cycles the credit denomination and rescales CRÉDITO to match — on "
    ".90 it stepped 100 COP / 20 credits to 200 COP / 10. The repaint lands a "
    "beat after the click, so a quick screenshot makes it look dead. Anything that "
    "touches it must put the denomination back.",
    "CREDIT_DISPLAY": "CRÉDITO caption + value. Box deliberately stops above the "
    "opacity slider that sits directly underneath.",
    "OPACITY_SLIDER": "Tap anywhere along the track to set opacity; eye icons at "
    "both ends. Moved here from layout1's top-centre position.",
    "PANO": "Top-right 'PAÑO' (cloth) icon — the layout2 counterpart of layout1's "
    "LAYOUT_SWITCH game selector. Proven on .90: it swaps the cloth for layout1, "
    "and only layout1's own selector brings this skin back.",
    "MENU": "Top-right gears icon. Dims the game and lays four slabs across the "
    "cloth (MENU_*); a second tap on MENÚ closes it again. Proven on .90.",
    "MENU_ASSISTANT": "'LLAMAR AL ASISTENTE' on the MENÚ overlay — calls an "
    "attendant, per the game's own help book. Left unclicked: a service call on a "
    "shared lab cabinet cannot be taken back from software.",
    "MENU_IDIOMA": "'IDIOMA' on the MENÚ overlay — the same language chooser the "
    "top-bar IDIOMA icon opens.",
    "MENU_AYUDA": "'AYUDA' on the MENÚ overlay — the same help book the top-bar "
    "AYUDA icon opens.",
    "MENU_PIN_LOCK": "'PIN LOCK' on the MENÚ overlay — locks the cabinet behind a "
    "PIN. Never automate: nobody here knows the PIN that unlocks it again.",
    "AYUDA": "Top-right '?' icon — opens the INFORMACIÓN DEL JUEGO help book "
    "(HELP_*), which is also the best written source for what every control does. "
    "Proven on .90; only SALIR closes it.",
    "HELP_TITLE": "'INFORMACIÓN DEL JUEGO' heading of the help book.",
    "HELP_INTRO": "Paragraph explaining the betting window, under the heading.",
    "HELP_LEGEND": "The legend grid: one row per control, with the game's own "
    "description of it. Read-only.",
    "HELP_BACK": "ATRÁS — previous help page.",
    "HELP_EXIT": "SALIR — the only way out of the help book; Escape and a second "
    "tap on AYUDA both leave it open.",
    "HELP_NEXT": "SIGUIENTE — next help page.",
    "COBRAR": "Cash out — never automate.",
    "STATISTICS": "Caption above the last-numbers capsule. Tapping it on .90 did "
    "nothing: the way into the statistics panel is the JUGADOR badge (PLAYER).",
    "HISTORY_STRIP": "Last-numbers capsule: no paging chevrons on this skin, and "
    "neither a tap nor a 1.2 s hold did anything on .90 (layout1 opens statistics "
    "on hold). Read it, do not click it.",
    "PLAYER": "JUGADOR badge — opens the full-screen ESTADÍSTICAS panel (STATS_*). "
    "Proven on .90; only SALIR closes it again.",
    "STATS_TITLE": "'ESTADÍSTICAS' heading on the statistics panel.",
    "STATS_LAST_NUMBERS": "ÚLTIMOS NÚMEROS grid on the statistics panel.",
    "STATS_HOT": "Hot-numbers capsule (flame) on the statistics panel.",
    "STATS_COLD": "Cold-numbers capsule (snow) on the statistics panel.",
    "STATS_EXIT": "SALIR — the only way out of the statistics panel; Escape and a "
    "second tap on the badge both leave it open.",
    "IDIOMA": "Opens the LANGUAGE chooser (LANG_*) over the cloth. Proven on .90.",
    "LANG_TITLE": "'LANGUAGE' header of the chooser IDIOMA opens.",
    "LANG_ES": "ESPAÑOL row of the language chooser; picking it closes the dialog.",
    "LANG_EN": "ENGLISH row of the language chooser; picking it switches the game "
    "to English, so use ESPAÑOL to dismiss unless a language change is wanted.",
    "CHANGE_VIEW": "'CAMBIAR VISTA' on the right rail (square <-> race). layout1 "
    "puts this at the bottom-right of the cloth instead. The swap lands a second "
    "or two after the click, so an early screenshot makes it look dead.",
    "SERIES_EYE_TOP": "Open-eye glyph above the right-rail SERIES caption. It does "
    "not 'show': it toggles the VECINOS DEL 0 / HUÉRFANOS / VECINOS DEL 00 pills, "
    "exactly like the crossed eye below it. Proven on .90 by tapping it twice.",
    "SERIES_EYE_BOTTOM": "Crossed-eye glyph below the right-rail SERIES caption; "
    "same toggle as the open eye above it.",
    "SERIES_EXTRA_EYE_TOP": "Open-eye glyph above the left-rail SERIES EXTRA "
    "caption; toggles the VECINOS / FINALES / COMPLETO pills.",
    "SERIES_EXTRA_EYE_BOTTOM": "Crossed-eye glyph below the left-rail SERIES EXTRA "
    "caption; same toggle as the open eye above it.",
    "VECINOS": "Left-rail pill. Only exists while the SERIES EXTRA rail is toggled "
    "on — with the rail off the cloth here is bare and a click does nothing.",
    "FINALES": "Left-rail pill (bets every number sharing the last digit of the "
    "last straight bet). Needs the SERIES EXTRA rail toggled on.",
    "COMPLETO": "Left-rail pill (every combination involving the last straight "
    "bet). Needs the SERIES EXTRA rail toggled on.",
    "PAYTABLE": "Green 'STANDARD 36X' tile. The help book calls it a paytable "
    "chooser and it greys out with the betting window, but tapping it inside an "
    "open window on .90 changes nothing: this cabinet ships one paytable.",
    "START": "Bottom-right START button; layout1 uses a top-centre countdown disc.",
    "EDGE_TAB": "White tab clipped by the right screen edge with a '<->' glyph; "
    "purpose unknown, so it is flagged avoid.",
}


def _cell_center(box: tuple[int, int, int, int]) -> tuple[int, int]:
    x0, y0, x1, y1 = box
    return (x0 + x1) // 2, (y0 + y1) // 2


def _box_entry(
    button_id: str,
    box: tuple[int, int, int, int],
    *,
    kind: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Registry entry for an explicit pixel box (used for inside-bet anchors)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx, cy = _cell_center(box)
    entry: dict[str, Any] = {
        "id": button_id,
        "x": x0,
        "y": y0,
        "width": w,
        "height": h,
        "click_center_px": {"x": cx, "y": cy},
        "click_center_pct": {
            "x_pct": round(100.0 * cx / LAYOUT2.client_w, 3),
            "y_pct": round(100.0 * cy / LAYOUT2.client_h, 3),
        },
        "click_area_pct": area_pct_dict(
            100.0 * x0 / LAYOUT2.client_w,
            100.0 * y0 / LAYOUT2.client_h,
            100.0 * x1 / LAYOUT2.client_w,
            100.0 * y1 / LAYOUT2.client_h,
        ),
        "kind": kind,
        "client": dict(CLIENT),
    }
    pb = place_bet_for_button(button_id)
    if pb:
        entry["place_bet"] = pb
    if extra:
        entry.update(extra)
    return entry


def _col_center(col: int) -> int:
    return (COL_X[col] + COL_X[col + 1]) // 2


def _row_center(row: int) -> int:
    return (ROW_Y[row] + ROW_Y[row + 1]) // 2


def inside_anchor_boxes() -> dict[str, tuple[int, int, int, int]]:
    """
    Splits / streets / corners / six-lines on the exact shared cell edges.

    Godot hit-tests these as thin strips straddling a separator line, so each
    anchor is a narrow box centred on the real line rather than a cell centre.
    """
    out: dict[str, tuple[int, int, int, int]] = {}
    half = 14  # half-thickness of a line strip

    # Vertical splits: two stacked cells in one grid column share a row line.
    for col in range(12):
        cx = _col_center(col)
        span = (COL_X[col + 1] - COL_X[col]) // 4
        for row in (0, 1):
            a = col * 3 + (3 - row)  # top row holds 3,6,9...
            b = a - 1
            y = ROW_Y[row + 1]
            lo, hi = sorted((a, b))
            out[f"split_{lo}_{hi}"] = (cx - span, y - half, cx + span, y + half)

    # Horizontal splits: neighbouring columns in one row share a column line.
    for row in range(3):
        cy = _row_center(row)
        span = (ROW_Y[row + 1] - ROW_Y[row]) // 4
        for col in range(11):
            a = col * 3 + (3 - row)
            b = (col + 1) * 3 + (3 - row)
            x = COL_X[col + 1]
            lo, hi = sorted((a, b))
            out[f"split_{lo}_{hi}"] = (x - half, cy - span, x + half, cy + span)

    # Streets: the outer edge of a 3-number column. layout2 draws the dozens
    # below the grid, so that outer line is the bottom edge.
    for col in range(12):
        cx = _col_center(col)
        span = (COL_X[col + 1] - COL_X[col]) // 4
        a = col * 3 + 1
        out[f"street_{a}_{a+1}_{a+2}"] = (cx - span, ROW_Y[3] - half, cx + span, ROW_Y[3] + half)

    # Corners: where four cells meet.
    for col in range(11):
        x = COL_X[col + 1]
        for row in (0, 1):
            y = ROW_Y[row + 1]
            a = col * 3 + (3 - row)
            b = a - 1
            c = (col + 1) * 3 + (3 - row)
            d = c - 1
            key = "corner_" + "_".join(str(n) for n in sorted((a, b, c, d)))
            out[key] = (x - half, y - half, x + half, y + half)

    # Six-lines: outer edge shared by two adjacent streets.
    for col in range(11):
        x = COL_X[col + 1]
        a = col * 3 + 1
        out[f"sixline_{a}_{a+5}"] = (x - half, ROW_Y[3] - half, x + half, ROW_Y[3] + half)

    # Zero edges, all proven live on .90. 00 covers the upper half of the zero
    # column and 0 the lower half, so each grid cell's stretch of the column line
    # is its own bet — except cell 2, which straddles the 0/00 divider and is a
    # single three-number bet (``Split 0+2+37``) along its whole edge.
    zx = ZERO_X[1]

    def _zero_edge(name: str, cell_row: int) -> None:
        y0, y1 = ROW_Y[cell_row], ROW_Y[cell_row + 1]
        cy = (y0 + y1) // 2
        reach = min(30, (y1 - y0) // 2)
        out[name] = (zx - half, cy - reach, zx + half, cy + reach)

    _zero_edge("split_3_37", 0)
    _zero_edge("split_0_2_37", 1)
    _zero_edge("split_0_1", 2)
    out["split_0_37"] = (
        (ZERO_X[0] + ZERO_X[1]) // 2 - 40,
        ZERO_SPLIT_Y - half,
        (ZERO_X[0] + ZERO_X[1]) // 2 + 40,
        ZERO_SPLIT_Y + half,
    )
    # The five-number bet is at the outer bottom corner of the zero column, not
    # at the 0/00 divider: a click at the divider returns Split 0+2+37, while
    # (244, 684) returns Basket 0+1+2+3+37. The matching top corner is dead.
    out["basket_0_1_2_3_37"] = (zx - half, ROW_Y[3] - half, zx + half, ROW_Y[3] + half)
    return out


def build_buttons() -> dict[str, dict[str, Any]]:
    """Every layout2 button: bets, outside, UI chrome, slider, inside anchors."""
    buttons: dict[str, dict[str, Any]] = {}

    for button_id in [*_ZEROS, *(str(n) for n in range(1, 37))]:
        fields = LAYOUT2.build_hitbox_fields(button_id, note=NOTES.get(button_id, ""))
        if fields is None:
            continue
        buttons[button_id] = {
            "id": button_id,
            **fields,
            "place_bet": place_bet_for_button(button_id),
            "chip": "chip_1",
            "chip_index": 0,
            "clear": "CancelAllBets",
        }

    for button_id in LAYOUT2.outside:
        fields = LAYOUT2.build_hitbox_fields(button_id)
        if fields is None:
            continue
        buttons[button_id] = {
            "id": button_id,
            **fields,
            "chip": "chip_1",
            "chip_index": 0,
            "clear": "CancelAllBets",
        }

    overlay_ids = [
        name
        for screen, boxes in LAYOUT2.overlays.items()
        if screen != "racetrack"
        for name in boxes
    ]
    for button_id in (*LAYOUT2.ui, *overlay_ids):
        fields = LAYOUT2.build_hitbox_fields(button_id, note=NOTES.get(button_id, ""))
        if fields is None:
            continue
        buttons[button_id] = {"id": button_id, **fields}

    # The racetrack view carries bets, not chrome: the oval's pockets and the
    # small cloth drawn inside it. Both stake real money, so they carry a PlaceBet
    # and stay unproven until the middleware names them.
    for button_id, box in (LAYOUT2.overlays.get("racetrack") or {}).items():
        pocket = button_id.startswith("race_")
        spot = button_id.removeprefix("race_").removeprefix("mini_")
        place = place_bet_for_button(spot)
        where = (
            f"Wheel pocket {spot} on the CAMBIAR VISTA racetrack"
            if pocket
            else f"'{spot}' on the small cloth inside the racetrack oval"
        )
        buttons[button_id] = _box_entry(
            button_id,
            box,
            kind="race_unverified",
            extra={
                "overlay": "racetrack",
                # Outside bets have no PlaceBet grammar of their own; they are
                # placed by clicking, so the key is simply left off for them.
                **({"place_bet": place} if place else {}),
                "note": f"{where}; coverage not proven against the middleware yet.",
                "chip": "chip_1",
                "chip_index": 0,
                "clear": "CancelAllBets",
            },
        )

    for button_id, box in inside_anchor_boxes().items():
        buttons[button_id] = _box_entry(
            button_id,
            box,
            kind="anchor_unverified",
            extra={
                "note": "Geometric anchor on the shared cell edge; coverage not "
                "proven against the middleware yet.",
                "chip": "chip_1",
                "chip_index": 0,
                "clear": "CancelAllBets",
            },
        )

    return buttons


def _merge_prior(buttons: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Keep live proof (``verified``/``expect``/``hit_count``) from a prior run."""
    if not REGISTRY.is_file():
        return buttons
    prev = json.loads(REGISTRY.read_text(encoding="utf-8-sig")).get("buttons") or {}
    for name, entry in buttons.items():
        old = prev.get(name)
        if not isinstance(old, dict):
            continue
        for key in ("verified", "expect", "hit_count", "proof", "aliases"):
            if key in old and key not in entry:
                entry[key] = old[key]
        # A live-verified entry keeps its proven kind and its note, which records
        # what the middleware reported — both are worth more than what this mapper
        # can say about a freshly generated box.
        if isinstance(old.get("verified"), dict) and old["verified"].get("reported_numbers"):
            if old.get("note"):
                entry["note"] = old["note"]
            proven = {"anchor_unverified": "anchor", "race_unverified": "race"}
            if old.get("kind") == proven.get(entry.get("kind")):
                entry["kind"] = old["kind"]
    return buttons


def catalog_counts(buttons: dict[str, dict[str, Any]]) -> dict[str, int]:
    kinds: dict[str, int] = {}
    for entry in buttons.values():
        kinds[str(entry.get("kind"))] = kinds.get(str(entry.get("kind")), 0) + 1
    return dict(sorted(kinds.items()))


def _prior_status() -> dict[str, Any]:
    if not REGISTRY.is_file():
        return {}
    status = json.loads(REGISTRY.read_text(encoding="utf-8-sig")).get("status")
    return status if isinstance(status, dict) else {}


def write_registry() -> dict[str, Any]:
    buttons = _merge_prior(build_buttons())
    out = {
        "layout_id": "layout2",
        "skin": LAYOUT2.skin,
        "layer": 1,
        "view": "square",
        "description": LAYOUT2.description,
        "buttons": buttons,
        "catalog": catalog_counts(buttons),
        "client": dict(CLIENT),
        "status": {
            **_prior_status(),
            "geometry_source": "_tmp_logs/layout2/screen_base.png separator-line detection",
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
        "note": "Cloth lines: vertical 124,244,363,483,602,721,841,960,1080,1199,"
        "1319,1438,1558,1678,1799; horizontal 324,443,564,684,803,922 (zero split 504). "
        "Dozens sit below the grid on this skin and START is bottom-right.",
    }
    REGISTRY.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Write the layout2 hitbox registry")
    p.add_argument("--print", action="store_true", help="List every mapped id")
    args = p.parse_args(argv)
    out = write_registry()
    if args.print:
        for name, entry in out["buttons"].items():
            c = entry.get("click_center_px") or {}
            print(f"  {name:24s} {entry.get('kind'):18s} @ {c.get('x')},{c.get('y')}")
    print(
        json.dumps(
            {"registry": str(REGISTRY), "buttons": len(out["buttons"]), "catalog": out["catalog"]},
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
