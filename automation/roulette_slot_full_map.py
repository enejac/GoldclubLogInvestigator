"""
Build the **slot** (OneHand RouletteGame) hitbox registry offline.

Geometry comes from :mod:`automation.roulette_slot_areas` (European single-zero
cloth on the bottom panel of the 1920×3240 client). Live click-proof against
middleware is a later step — Slot Roulette does not speak Godot ``:8090``.

    python -m automation.roulette_slot_full_map
    python -m automation.roulette_map_overlay --layout slot --input _tmp_logs/slot_roulette/play_client.png

Registry out: ``automation/layouts/slot_hitboxes.json``
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from automation.roulette_bet_catalog import place_bet_for_button
from automation.roulette_slot_areas import LAYOUT_SLOT
from automation.roulette_surface import surface_for

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "automation" / "layouts" / "slot_hitboxes.json"
CLIENT = {"width": LAYOUT_SLOT.client_w, "height": LAYOUT_SLOT.client_h}

NOTES: dict[str, str] = {
    "SHOW_WINS": "Eye pill left of the dozens (ES: MOSTRAR PREMIOS). Geometry from "
    "panel_bot_cloth; effect not proven live yet.",
    "HOT_COLD": "Flame|snowflake pill: flashes 5 hot + 5 cold highlights on the cloth for ~5 s. Bot reads them via roulette_slot_hotcold.",
    "CHANGE_VIEW": "Green pill right of 2to1 (ES: CAMBIAR VISTA).",
    "MORE_GAMES": "Footer exit to multigamer (ES: MÁS JUEGOS). Forbidden.",
    "SPIN": "Footer spin (ES: JUGAR). Mandatory to resolve a round; SPIN with an empty board rebets the last stake.",
    "HELP": "Footer help (ES: AYUDA).",
    "HELP_BACK": "BACK TO GAME — closes the help book (only reliable exit).",
    "HELP_PREV": "<< page of the help book.",
    "HELP_NEXT": ">> page of the help book.",
    "HELP_PANEL": "Help book content (INSIDE BETS / …). Read-only.",
    "HISTORY_BTN": "Opens HISTORY / LAST 100 NUMBERS modal.",
    "HISTORY_CLOSE": "CLOSE on the LAST 100 modal.",
    "HISTORY_PANEL": "5x20 last-100 chips (newest top-left). Read-only.",
    "NEIGHBORS": "Call bet (ES: VECINOS).",
    "NEIGHBORS_OF_ZERO": "Call bet (ES: VECINOS DEL CERO).",
    "ZERO_GAME": "Call bet (ES: CERO JUEGO).",
    "SERIES_5_8": "Call bet (ES: SERIE 5/8).",
    "ERASER": "Undo last chip (ES: BORRADOR).",
    "DOUBLE": "x2 DOUBLE — doubles current board stakes (was mislabeled REPEAT).",
    "CHANGE_VIEW": "Toggles the wheel-order racetrack oval around the square cloth.",
    "CANCEL_ALL": "Clear board (ES: BORRAR TODO).",
    "DOUBLE": "Repeat last bet (ES: REPETIR).",
}


def build_buttons() -> dict[str, dict[str, Any]]:
    surface = surface_for("slot")
    buttons: dict[str, dict[str, Any]] = {}

    for button_id in ("0", *(str(n) for n in range(1, 37))):
        fields = surface.build_hitbox_fields(button_id, note=NOTES.get(button_id, ""))
        if fields is None:
            continue
        place = place_bet_for_button(button_id)
        buttons[button_id] = {
            "id": button_id,
            **fields,
            **({"place_bet": place} if place else {}),
            "chip": "chip_20",
            "chip_index": 0,
            "clear": "CANCEL_ALL",
        }

    for button_id in surface.outside:
        fields = surface.build_hitbox_fields(button_id)
        if fields is None:
            continue
        buttons[button_id] = {
            "id": button_id,
            **fields,
            "chip": "chip_20",
            "chip_index": 0,
            "clear": "CANCEL_ALL",
        }

    overlay_ids = [
        name
        for screen, boxes in surface.overlays.items()
        if screen != "statistics"
        for name in boxes
    ]
    for button_id in (*surface.ui, *overlay_ids):
        fields = surface.build_hitbox_fields(button_id, note=NOTES.get(button_id, ""))
        if fields is None:
            continue
        buttons[button_id] = {"id": button_id, **fields}

    for button_id in surface.stats:
        fields = surface.build_hitbox_fields(button_id, note=NOTES.get(button_id, ""))
        if fields is None:
            continue
        buttons[button_id] = {"id": button_id, **fields}

    return buttons


def _merge_prior(buttons: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
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
    return buttons


def catalog_counts(buttons: dict[str, dict[str, Any]]) -> dict[str, int]:
    kinds: dict[str, int] = {}
    for entry in buttons.values():
        kinds[str(entry.get("kind"))] = kinds.get(str(entry.get("kind")), 0) + 1
    return dict(sorted(kinds.items()))


def write_registry() -> dict[str, Any]:
    buttons = _merge_prior(build_buttons())
    out = {
        "layout_id": "slot",
        "skin": LAYOUT_SLOT.skin,
        "layer": 1,
        "view": "cloth",
        "description": LAYOUT_SLOT.description,
        "buttons": buttons,
        "catalog": catalog_counts(buttons),
        "client": dict(CLIENT),
        "status": {
            "geometry_source": "_tmp_logs/slot_roulette/panel_bot_cloth.png colour runs",
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "proof": "geometry only — Slot Roulette has no Godot :8090 oracle yet",
        },
        "note": "European single-zero. Interactive cloth on bottom panel y=2160..3240. "
        "Remote screen capture stitches two 1080p monitors (wheel+cloth = 2160); "
        "click coords stay in full 3240 client space.",
    }
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Write the Slot Roulette hitbox registry")
    p.add_argument("--print", action="store_true", help="List every mapped id")
    args = p.parse_args(argv)
    out = write_registry()
    if args.print:
        for name, entry in out["buttons"].items():
            c = entry.get("click_center_px") or {}
            print(f"  {name:24s} {entry.get('kind'):18s} @ {c.get('x')},{c.get('y')}")
    print(
        json.dumps(
            {
                "registry": str(REGISTRY),
                "buttons": len(out["buttons"]),
                "catalog": out["catalog"],
            },
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
