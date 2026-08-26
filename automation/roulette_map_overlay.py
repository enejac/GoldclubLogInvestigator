"""
Draw the mapped clickable areas over a live roulette screenshot.

Renders translucent rectangles for every mapped hitbox so the mapping can be
reviewed against the real UI, with a legend panel beside the game (never on
top of it) and zoom insets for the controls that are easiest to confuse.

Works on any mapped skin: ``--layout`` selects which surface to draw, and the
cabinet must already be showing that skin for the capture to line up.

Usage:
  python -m automation.roulette_map_overlay                     # live capture, all views
  python -m automation.roulette_map_overlay --mode ui
  python -m automation.roulette_map_overlay --layout layout2 --mode all
  python -m automation.roulette_map_overlay --layout layout2 --mode anchors
  python -m automation.roulette_map_overlay --input shot.png --mode board
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app_paths import app_tmp_logs_dir
from automation.roulette_layout_store import load_hitboxes, resolve_hitbox_center
from automation.roulette_surface import (
    DEFAULT_LAYOUT,
    KNOWN_LAYOUTS,
    Surface,
    surface_for,
)
from network.screen_capture import capture_remote_screen

REPO = Path(__file__).resolve().parents[1]
IP_DEFAULT = "10.0.0.90"
PANEL_W = 430


def out_dir_for(layout_id: str) -> Path:
    """layout1 keeps the original paths; other skins render into a subfolder."""
    lid = (layout_id or DEFAULT_LAYOUT).strip().lower()
    base = app_tmp_logs_dir() / "map_overlay"
    return base if lid == DEFAULT_LAYOUT else base / lid


def verify_results_for(layout_id: str) -> Path:
    lid = (layout_id or DEFAULT_LAYOUT).strip().lower()
    base = app_tmp_logs_dir() / "verify_bets"
    return (base if lid == DEFAULT_LAYOUT else base / lid) / "results.json"


# Buttons whose on-screen caption already names them; a tag would just add noise.
SELF_LABELLED = {
    "IDIOMA",
    "COBRAR",
    "AYUDA",
    "LLAMAR",
    "MENU",
    "PANO",
    "VECINOS",
    "FINALES",
    "COMPLETO",
    "VECINOS_0",
    "HUERFANOS",
    "VECINOS_00",
    "BORRADOR",
    "CANCELAR_TODO",
    "REPETIR",
    "DENOM",
    "MUESTRA_GANANCIAS",
    "CALIENTE_FRIO",
    "APUESTAS_PREDEFINIDAS",
    "START",
    # Top-bar readouts and chevrons: the on-screen caption or glyph is the label.
    "LAST_BET",
    "LAST_WIN",
    "PLAYER",
    "HISTORY_PREV",
    "HISTORY_NEXT",
}

# Boxes whose caption sits at the top, so the tag goes along the bottom edge.
BOTTOM_LABELS = {"CREDIT_DISPLAY", "STATISTICS"}

# Tags for boxes whose id is too long to fit, or that need a hint about behaviour.
SHORT_TAGS = {
    "CREDIT_DISPLAY": "CREDIT (tap: $ / cr)",
    "STATISTICS": "STATS",
    "HISTORY_PREV": "<",
    "HISTORY_NEXT": ">",
    "OPACITY_SLIDER": "OPACITY",
    "LAST_BET": "L.BET",
    "LAST_WIN": "L.WIN",
    "PLAYER": "PLAYER",
    "LAYOUT_SWITCH": "LAYOUT",
    "STATS_TAB": "TAB",
    "STATS_BACK": "BACK",
    "STATS_EXIT": "EXIT",
    "STATS_NEXT": "NEXT",
    "SERIES_EXTRA_EYE_TOP": "SER+ eye",
    "SERIES_EXTRA_EYE_BOTTOM": "SER+ eye2",
    "SERIES_EYE_TOP": "SER eye",
    "SERIES_EYE_BOTTOM": "SER eye2",
    "MENU_ASSISTANT": "ASIST",
    "MENU_IDIOMA": "M-IDIOMA",
    "MENU_AYUDA": "M-AYUDA",
    "MENU_PIN_LOCK": "PIN LOCK",
    "HELP_TITLE": "HELP",
    "HELP_INTRO": "INTRO",
    "HELP_LEGEND": "LEGEND",
    "HELP_BACK": "ATRAS",
    "HELP_EXIT": "SALIR",
    "HELP_NEXT": "SIGUIENTE",
    "EDGE_TAB": "?",
}

RED = (235, 64, 52)
CYAN = (0, 229, 255)
YELLOW = (255, 205, 0)
GREEN = (40, 220, 120)
BLUE = (90, 160, 255)
PURPLE = (200, 110, 255)
ORANGE = (255, 145, 30)
GREY = (170, 170, 170)
WHITE = (255, 255, 255)
PINK = (255, 120, 200)
ROSE = (190, 95, 140)
STEEL = (120, 130, 155)
LIME = (200, 255, 60)
TEAL = (60, 220, 200)

# group -> (colour, description)
GROUPS: dict[str, tuple[tuple[int, int, int], str]] = {
    "straight": (GREEN, "Straight numbers 1-36"),
    # 0/00 sit on green cloth, so they need a colour that survives the felt.
    "zero": (WHITE, "0 / 00 green pockets"),
    "dozen": (BLUE, "Dozens + columns (2 a 1)"),
    "even": (PURPLE, "Even-money outside bets"),
    "chip": (YELLOW, "Chip denominations"),
    "clear": (RED, "Clear / repeat controls"),
    "layout": (CYAN, "Skin selector (LAYOUT_SWITCH / PANO)"),
    "view": (ORANGE, "CHANGE_VIEW (square <-> race)"),
    "ui": (GREY, "Other UI chrome"),
    "history": (LIME, "History strip (drag) + end arrows"),
    "stats": (CYAN, "Statistics panel (modal screen)"),
    "overlay": (LIME, "Sub-screens (language chooser)"),
    "readout": (STEEL, "Read-only readouts (never click)"),
    "slider": (PINK, "OPACIDAD slider (tap along track)"),
    "rail": (TEAL, "SERIES rails (show / hide eyes)"),
    "avoid": (ROSE, "Avoid: hold-sensitive controls"),
    "forbidden": ((255, 0, 90), "Never automate (COBRAR/LLAMAR/LOCK)"),
}

# Drawn as zoom insets when the skin has them.
ZOOM_CANDIDATES = (("LAYOUT_SWITCH", CYAN), ("PANO", CYAN), ("CHANGE_VIEW", ORANGE))

# verdict -> (colour, description)
VERDICTS: dict[str, tuple[tuple[int, int, int], str]] = {
    "verified": (GREEN, "Verified live: middleware reported the exact bet"),
    "mismatch": (RED, "Wrong bet reported - remap needed"),
    "blocked": (ORANGE, "Not testable now (needs >= 10 credits)"),
    "untested": (STEEL, "Not tested in this run"),
}

HISTORY_NAMES = ("HISTORY_PREV", "HISTORY_NEXT", "HISTORY_STRIP")
RAIL_NAMES = (
    "SERIES_EYE_TOP", "SERIES_EYE_BOTTOM",
    "SERIES_EXTRA_EYE_TOP", "SERIES_EXTRA_EYE_BOTTOM",
)


def _group_of(name: str, surface: Surface) -> str:
    if name in surface.forbidden:
        return "forbidden"
    if name in surface.sliders:
        return "slider"
    if name in HISTORY_NAMES:
        return "history"
    if name in RAIL_NAMES:
        return "rail"
    if name in surface.info_only:
        return "readout"
    if name in surface.avoid:
        return "avoid"
    if name in surface.stats:
        return "stats"
    if surface.overlay_of(name):
        return "overlay"
    if name in ("0", "00"):
        return "zero"
    if name in ("LAYOUT_SWITCH", "PANO"):
        return "layout"
    if name == "CHANGE_VIEW":
        return "view"
    if name in ("CANCELAR_TODO", "BORRADOR", "REPETIR"):
        return "clear"
    if name.startswith("chip_"):
        return "chip"
    if name in surface.ui:
        return "ui"
    if name in surface.outside:
        return "dozen" if ("2to1" in name or "-12" in name or "-24" in name or "-36" in name) else "even"
    return "straight"


def _font(size: int):
    for name in ("segoeuib.ttf", "arialbd.ttf", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _grab(ip: str, out_dir: Path, client: tuple[int, int] = (1920, 1080)) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / "screen_raw.jpg"
    ok, msg = capture_remote_screen(ip, str(raw))
    if not ok:
        raise RuntimeError(f"capture failed: {msg}")
    im = Image.open(raw).convert("RGB")
    if im.size[1] >= 2000:
        im = im.crop((0, 0, client[0], client[1]))
    elif im.size != client:
        im = im.resize(client, Image.LANCZOS)
    base = out_dir / "screen_base.png"
    im.save(base)
    return base


def _areas_for_mode(mode: str, surface: Surface) -> dict[str, tuple[int, int, int, int]]:
    if mode == "board":
        return surface.bet_areas_px
    if mode == "ui":
        return dict(surface.ui)
    if mode == "stats":
        return dict(surface.stats)
    # A sub-screen only makes sense drawn over a capture of that screen, so these
    # modes are meant to be used with --input.
    if mode.startswith("screen:"):
        return dict(surface.overlays.get(mode.split(":", 1)[1], {}))
    return {**surface.bet_areas_px, **surface.ui}


def _draw_label(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    colour: tuple[int, int, int],
    font,
    *,
    bottom: bool = False,
) -> None:
    """Draw a small tag inside the box, only when it comfortably fits."""
    x0, y0, x1, y1 = box
    tw = draw.textlength(text, font=font)
    th = font.size + 2
    if tw + 8 > (x1 - x0) or th + 6 > (y1 - y0):
        return
    ty = (y1 - th - 5) if bottom else (y0 + 2)
    draw.rectangle((x0 + 2, ty, x0 + 6 + tw, ty + 2 + th), fill=(0, 0, 0, 190))
    draw.text((x0 + 4, ty + 1), text, font=font, fill=colour + (255,))


def _inset_height(
    y: int, desired: int, client_h: int, *, caption_h: int = 62, minimum: int = 44
) -> int:
    """Shrink an inset so the panel never runs off the bottom of the canvas."""
    room = client_h - y - caption_h
    if room < minimum:
        return 0
    return min(desired, room)


def _zoom_inset(
    base: Image.Image, box: tuple[int, int, int, int], size: tuple[int, int]
) -> Image.Image:
    x0, y0, x1, y1 = box
    pad = 18
    crop = base.crop(
        (
            max(0, x0 - pad),
            max(0, y0 - pad),
            min(base.width, x1 + pad),
            min(base.height, y1 + pad),
        )
    )
    return crop.resize(size, Image.LANCZOS)


def render(base_path: Path, *, mode: str, layout_id: str = DEFAULT_LAYOUT) -> Path:
    surface = surface_for(layout_id)
    client = (surface.client_w, surface.client_h)
    out_dir = out_dir_for(layout_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    base = Image.open(base_path).convert("RGB")
    canvas = Image.new("RGB", (client[0] + PANEL_W, client[1]), (16, 16, 20))
    canvas.paste(base, (0, 0))

    overlay = Image.new("RGBA", client, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    f_tag = _font(15)

    areas = _areas_for_mode(mode, surface)
    counts: dict[str, int] = {}

    for name, box in sorted(areas.items()):
        group = _group_of(name, surface)
        colour, _ = GROUPS[group]
        counts[group] = counts.get(group, 0) + 1
        x0, y0, x1, y1 = box
        alpha = 26 if group in ("straight", "even", "dozen") else 55
        width = 3 if group in ("layout", "view", "clear", "forbidden", "zero") else 2
        draw.rectangle((x0, y0, x1, y1), fill=colour + (alpha,), outline=colour + (255,), width=width)

    # Labels drawn after all boxes so they stay on top.
    for name, box in sorted(areas.items()):
        if name in SELF_LABELLED:
            continue
        group = _group_of(name, surface)
        colour, _ = GROUPS[group]
        tag = SHORT_TAGS.get(name, name[:16])
        if mode == "board" or group not in ("straight", "zero", "even", "dozen"):
            _draw_label(draw, tag, box, colour, f_tag, bottom=name in BOTTOM_LABELS)

    # Actual click point used by automation (may be off-centre when verified live).
    for name, box in sorted(areas.items()):
        if mode != "board" and name not in surface.ui and name not in surface.stats:
            continue
        target = resolve_hitbox_center(name, layout_id)
        if target is None:
            continue
        cx = int(target.x_pct / 100 * client[0])
        cy = int(target.y_pct / 100 * client[1])
        colour, _ = GROUPS[_group_of(name, surface)]
        draw.line((cx - 6, cy, cx + 6, cy), fill=(255, 255, 255, 230), width=2)
        draw.line((cx, cy - 6, cx, cy + 6), fill=(255, 255, 255, 230), width=2)
        draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=colour + (255,))

    # Sliders: the box alone says nothing about where a tap lands, so draw the
    # handle travel with a tick at value 0.0 and 1.0.
    for name, spec in surface.sliders.items():
        if name not in areas:
            continue
        colour, _ = GROUPS["slider"]
        lo, hi, ty = spec["handle_min_x"], spec["handle_max_x"], spec["track_y"]
        draw.line((lo, ty, hi, ty), fill=colour + (235,), width=3)
        for x, tag in ((lo, "0.0"), (hi, "1.0")):
            draw.line((x, ty - 13, x, ty + 13), fill=colour + (255,), width=3)
            tw = draw.textlength(tag, font=f_tag)
            draw.rectangle((x - tw / 2 - 3, ty + 15, x + tw / 2 + 3, ty + 17 + f_tag.size), fill=(0, 0, 0, 200))
            draw.text((x - tw / 2, ty + 16), tag, font=f_tag, fill=colour + (255,))

    # Extra glow rings for the controls that are easiest to miss or confuse.
    for name, colour in (
        ("LAYOUT_SWITCH", CYAN),
        ("PANO", CYAN),
        ("HISTORY_PREV", LIME),
        ("HISTORY_NEXT", LIME),
    ):
        if name not in areas:
            continue
        gx0, gy0, gx1, gy1 = surface.ui[name]
        for pad in (5, 9):
            draw.rectangle(
                (gx0 - pad, gy0 - pad, gx1 + pad, gy1 + pad),
                outline=colour + (150 - pad * 8,),
                width=2,
            )

    canvas.paste(Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB"), (0, 0))

    # ---- side panel ------------------------------------------------------
    pd = ImageDraw.Draw(canvas)
    px0 = client[0]
    f_h1 = _font(26)
    f_h2 = _font(18)
    f_sm = _font(15)
    y = 22
    pd.text((px0 + 20, y), f"{surface.layout_id} hitboxes", font=f_h1, fill=(255, 255, 255))
    y += 30
    pd.text((px0 + 20, y), surface.skin, font=f_sm, fill=(150, 200, 255))
    y += 22
    pd.text(
        (px0 + 20, y),
        f"view: {mode}   client {client[0]}x{client[1]}",
        font=f_sm,
        fill=(150, 200, 255),
    )
    y += 30

    for group, (colour, desc) in GROUPS.items():
        n = counts.get(group, 0)
        if n == 0:
            continue
        pd.rectangle((px0 + 20, y + 3, px0 + 44, y + 21), fill=colour, outline=(255, 255, 255))
        pd.text((px0 + 54, y), f"{n:>3}  {desc}", font=f_sm, fill=(235, 235, 235))
        y += 23

    y += 10
    pd.text(
        (px0 + 20, y),
        "boxes = full clickable area\ncrosshair = point automation clicks",
        font=f_sm,
        fill=(150, 150, 160),
    )
    y += 52

    if surface.unproven:
        pd.text((px0 + 20, y), "Effect not proven live yet", font=f_h2, fill=ORANGE)
        y += 24
        for line in _wrap(", ".join(surface.unproven), 46):
            pd.text((px0 + 20, y), line, font=f_sm, fill=(230, 205, 170))
            y += 19
        y += 8

    pd.line((px0 + 20, y, px0 + PANEL_W - 20, y), fill=(70, 70, 80), width=2)
    y += 18

    for name, colour in ZOOM_CANDIDATES:
        if name not in areas:
            continue
        box = surface.ui[name]
        cx = (box[0] + box[2]) // 2
        cy = (box[1] + box[3]) // 2
        h = _inset_height(y + 26, 84, client[1])
        if h == 0:
            continue
        pd.text((px0 + 20, y), name, font=f_h2, fill=colour)
        y += 26
        canvas.paste(_zoom_inset(base, box, (PANEL_W - 44, h)), (px0 + 20, y))
        pd.rectangle((px0 + 20, y, px0 + 20 + PANEL_W - 44, y + h), outline=colour, width=3)
        y += h + 6
        pd.text(
            (px0 + 20, y),
            f"box {box[0]},{box[1]} - {box[2]},{box[3]}\n"
            f"click {100*cx/client[0]:.2f}% , {100*cy/client[1]:.2f}%",
            font=f_sm,
            fill=(200, 200, 200),
        )
        y += 42

    # Both history-strip ends side by side, cropped from the composited image so the
    # boxes are visible on glyphs that are only a dozen px wide on the full render.
    if (
        "HISTORY_PREV" in areas
        and "HISTORY_NEXT" in areas
        and _inset_height(y + 26, 92, client[1])
    ):
        pd.text((px0 + 20, y), "HISTORY ARROWS", font=f_h2, fill=LIME)
        y += 26
        left = _pad_crop(canvas, surface.ui["HISTORY_PREV"], client)
        right = _pad_crop(canvas, surface.ui["HISTORY_NEXT"], client)
        strip = Image.new("RGB", (left.width + right.width + 4, max(left.height, right.height)), (60, 60, 70))
        strip.paste(left, (0, 0))
        strip.paste(right, (left.width + 4, 0))
        inset_h = _inset_height(y, 92, client[1])
        inset_w = round(strip.width * inset_h / strip.height)
        canvas.paste(strip.resize((inset_w, inset_h), Image.LANCZOS), (px0 + 20, y))
        pd.rectangle((px0 + 20, y, px0 + 20 + inset_w, y + inset_h), outline=LIME, width=2)
        y += inset_h + 6
        hs = surface.ui.get("HISTORY_STRIP")
        pd.text(
            (px0 + 20, y),
            (f"strip {hs[0]},{hs[1]} - {hs[2]},{hs[3]} (drag to scroll)\n" if hs else "")
            + "arrows outside it; tap did not page on .90",
            font=f_sm,
            fill=(200, 200, 200),
        )
        y += 40

    # Slider travel, taken from the composited image so the ticks are visible.
    for name, spec in surface.sliders.items():
        if name not in areas:
            continue
        colour, _ = GROUPS["slider"]
        box = surface.ui[name]
        crop = canvas.crop(
            (
                max(0, box[0] - 12),
                max(0, box[1] - 8),
                min(client[0], box[2] + 12),
                min(client[1], box[3] + 26),
            )
        )
        inset_w = PANEL_W - 44
        inset_h = _inset_height(
            y + 26, round(crop.height * inset_w / crop.width), client[1], caption_h=46, minimum=36
        )
        if inset_h == 0:
            continue
        inset_w = round(crop.width * inset_h / crop.height)
        pd.text((px0 + 20, y), name, font=f_h2, fill=colour)
        y += 26
        canvas.paste(crop.resize((inset_w, inset_h), Image.LANCZOS), (px0 + 20, y))
        pd.rectangle((px0 + 20, y, px0 + 20 + inset_w, y + inset_h), outline=colour, width=2)
        y += inset_h + 6
        lo, hi, ty = spec["handle_min_x"], spec["handle_max_x"], spec["track_y"]
        pd.text(
            (px0 + 20, y),
            f"tap x {lo}..{hi} at y {ty} = value 0..1\n"
            f"({100*lo/client[0]:.2f}% .. {100*hi/client[0]:.2f}%)",
            font=f_sm,
            fill=(200, 200, 200),
        )
        y += 40

    # "screen:help" would create an NTFS alternate data stream, not a file.
    stem = f"overlay_{mode.replace(':', '_')}"
    out_path = out_dir / f"{stem}.png"
    canvas.save(out_path)
    meta: dict[str, Any] = {
        "layout_id": surface.layout_id,
        "skin": surface.skin,
        "mode": mode,
        "counts": counts,
        "areas": {k: list(v) for k, v in areas.items()},
    }
    (out_dir / f"{stem}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out_path


def _pad_crop(
    canvas: Image.Image, box: tuple[int, int, int, int], client: tuple[int, int], pad: int = 20
) -> Image.Image:
    return canvas.crop(
        (
            max(0, box[0] - pad),
            max(0, box[1] - pad),
            min(client[0], box[2] + pad),
            min(client[1], box[3] + pad),
        )
    )


def _verdict_of(res: dict[str, Any] | None) -> str:
    if res is None:
        return "untested"
    if res.get("ok") is True:
        return "verified"
    if res.get("ok") is None or res.get("skipped"):
        return "blocked"
    return "mismatch"


def render_verify(
    base_path: Path,
    *,
    layout_id: str = DEFAULT_LAYOUT,
    results_path: Path | None = None,
) -> Path:
    """
    Draw the live verification verdict for every bet spot.

    Green means a real click on that box made the middleware report exactly the
    numbers the box is supposed to cover; red means it reported something else.
    """
    surface = surface_for(layout_id)
    client = (surface.client_w, surface.client_h)
    out_dir = out_dir_for(layout_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = results_path or verify_results_for(layout_id)

    payload = json.loads(results_path.read_text(encoding="utf-8"))
    results = {str(r.get("id")): r for r in payload.get("results") or []}

    base = Image.open(base_path).convert("RGB")
    canvas = Image.new("RGB", (client[0] + PANEL_W, client[1]), (16, 16, 20))
    canvas.paste(base, (0, 0))
    overlay = Image.new("RGBA", client, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    f_tag = _font(15)

    areas = surface.bet_areas_px
    counts: dict[str, int] = {}
    for name, box in sorted(areas.items()):
        verdict = _verdict_of(results.get(name))
        counts[verdict] = counts.get(verdict, 0) + 1
        colour, _ = VERDICTS[verdict]
        x0, y0, x1, y1 = box
        alpha = 34 if verdict == "verified" else 60
        draw.rectangle(
            (x0, y0, x1, y1),
            fill=colour + (alpha,),
            outline=colour + (255,),
            width=3 if verdict != "verified" else 2,
        )

    for name, box in sorted(areas.items()):
        res = results.get(name)
        verdict = _verdict_of(res)
        colour, _ = VERDICTS[verdict]
        target = resolve_hitbox_center(name, layout_id)
        if target is not None:
            cx = int(target.x_pct / 100 * client[0])
            cy = int(target.y_pct / 100 * client[1])
            draw.line((cx - 7, cy, cx + 7, cy), fill=(255, 255, 255, 235), width=2)
            draw.line((cx, cy - 7, cx, cy + 7), fill=(255, 255, 255, 235), width=2)
            draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=colour + (255,))
        # Straights carry the bet id the core actually reported (00 -> 37).
        if verdict == "verified" and res:
            tag = f"OK {res.get('bet_id', '')}"
        elif verdict == "mismatch":
            tag = "MISMATCH"
        elif verdict == "blocked":
            tag = "NEEDS CR"
        else:
            tag = name[:12]
        _draw_label(draw, tag, box, colour, f_tag)

    canvas.paste(Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB"), (0, 0))

    pd = ImageDraw.Draw(canvas)
    px0 = client[0]
    f_h1 = _font(26)
    f_h2 = _font(18)
    f_sm = _font(15)
    y = 22
    pd.text((px0 + 20, y), f"{surface.layout_id} bet spots", font=f_h1, fill=(255, 255, 255))
    y += 30
    pd.text((px0 + 20, y), surface.skin, font=f_sm, fill=(150, 200, 255))
    y += 22
    pd.text(
        (px0 + 20, y),
        f"{payload.get('passed', 0)}/{payload.get('total', 0)} spots proven on "
        f"{payload.get('ip', '')}   {client[0]}x{client[1]}",
        font=f_sm,
        fill=(150, 200, 255),
    )
    y += 30

    for verdict, (colour, desc) in VERDICTS.items():
        n = counts.get(verdict, 0)
        if n == 0:
            continue
        pd.rectangle((px0 + 20, y + 3, px0 + 44, y + 21), fill=colour, outline=(255, 255, 255))
        pd.text((px0 + 54, y), f"{n:>3}  {desc}", font=f_sm, fill=(235, 235, 235))
        y += 40 if len(desc) > 42 else 23

    y += 12
    pd.text((px0 + 20, y), "How each box was proven", font=f_h2, fill=(255, 255, 255))
    y += 26
    pd.text(
        (px0 + 20, y),
        "1  chip_1 armed, cloth cleared\n"
        "2  real Godot click at the crosshair\n"
        "3  GET /api/data/0 -> PlayerDataBets\n"
        "4  reported numbers compared to the box\n"
        "5  CancelAllBets refunds the stake",
        font=f_sm,
        fill=(200, 200, 200),
    )
    y += 112
    pd.text(
        (px0 + 20, y),
        "Tag on each box is the bet id the core\n"
        "returned, so 00 reading 'OK 37' is correct.\n"
        "Betting window is 22 s of a 40 s cycle.",
        font=f_sm,
        fill=(150, 150, 160),
    )
    y += 78

    failed = [r for r in results.values() if r.get("ok") is False]
    blocked = [r for r in results.values() if r.get("ok") is None]
    if failed:
        pd.text((px0 + 20, y), "Mismatched spots", font=f_h2, fill=RED)
        y += 26
        for res in failed[:8]:
            pd.text(
                (px0 + 20, y),
                f"{res.get('id')}: {str(res.get('error'))[:44]}",
                font=f_sm,
                fill=(240, 190, 190),
            )
            y += 20
        y += 10
    if blocked:
        pd.text((px0 + 20, y), "Waiting on credits", font=f_h2, fill=ORANGE)
        y += 26
        ids = ", ".join(str(r.get("id")) for r in blocked)
        for line in _wrap(ids, 46):
            pd.text((px0 + 20, y), line, font=f_sm, fill=(240, 215, 180))
            y += 20
        y += 6
        pd.text(
            (px0 + 20, y),
            "Outside bets are dropped below 10 credits;\n"
            "top up the cabinet and re-run those ids.",
            font=f_sm,
            fill=(200, 200, 200),
        )

    out_path = out_dir / "overlay_verified.png"
    canvas.save(out_path)
    (out_dir / "overlay_verified.json").write_text(
        json.dumps(
            {"layout_id": surface.layout_id, "counts": counts, "results": payload.get("results")},
            indent=2,
        ),
        encoding="utf-8",
    )
    return out_path


ANCHOR_KINDS = {"anchor", "anchor_unverified"}

# Inside-bet family -> (colour, description). Each family lies on a different
# part of the cloth grid, so colouring by family shows misplaced boxes at a glance.
ANCHOR_GROUPS: dict[str, tuple[tuple[int, int, int], str]] = {
    "split": (CYAN, "Splits (2 numbers, one shared edge)"),
    "street": (YELLOW, "Streets (3 numbers, outer edge)"),
    "corner": (ORANGE, "Corners (4 numbers, cell junction)"),
    "sixline": (PINK, "Six-lines (6 numbers, outer junction)"),
    "basket": (WHITE, "Basket (0+00+1+2+3, outer corner)"),
}


def render_anchors(base_path: Path, *, layout_id: str = DEFAULT_LAYOUT) -> Path:
    """
    Draw the inside-bet anchors straight from the registry.

    These boxes are not part of the skin's surface (they straddle cell edges
    rather than filling a cell), so they come from ``{layout}_hitboxes.json``,
    tagged with the bet the middleware reported for each one.
    """
    surface = surface_for(layout_id)
    client = (surface.client_w, surface.client_h)
    out_dir = out_dir_for(layout_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    buttons = load_hitboxes(layout_id).get("buttons") or {}
    anchors = {
        name: entry for name, entry in buttons.items() if entry.get("kind") in ANCHOR_KINDS
    }

    base = Image.open(base_path).convert("RGB")
    canvas = Image.new("RGB", (client[0] + PANEL_W, client[1]), (16, 16, 20))
    canvas.paste(base, (0, 0))
    overlay = Image.new("RGBA", client, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    counts: dict[str, int] = {}
    unproven: list[str] = []
    for name, entry in sorted(anchors.items()):
        family = name.split("_")[0]
        colour, _ = ANCHOR_GROUPS.get(family, (GREY, family))
        counts[family] = counts.get(family, 0) + 1
        proven = bool((entry.get("verified") or {}).get("reported_numbers"))
        if not proven:
            unproven.append(name)
        x, y = int(entry["x"]), int(entry["y"])
        x1, y1 = x + int(entry["width"]), y + int(entry["height"])
        draw.rectangle(
            (x, y, x1, y1),
            fill=colour + (70 if proven else 20,),
            outline=colour + (255,),
            width=2 if proven else 1,
        )
        centre = entry.get("click_center_px") or {}
        cx, cy = int(centre.get("x", (x + x1) // 2)), int(centre.get("y", (y + y1) // 2))
        draw.line((cx - 4, cy, cx + 4, cy), fill=(255, 255, 255, 235), width=1)
        draw.line((cx, cy - 4, cx, cy + 4), fill=(255, 255, 255, 235), width=1)

    canvas.paste(Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB"), (0, 0))

    pd = ImageDraw.Draw(canvas)
    px0 = client[0]
    f_h1, f_h2, f_sm = _font(26), _font(18), _font(15)
    y = 22
    pd.text((px0 + 20, y), f"{surface.layout_id} inside bets", font=f_h1, fill=(255, 255, 255))
    y += 30
    pd.text((px0 + 20, y), surface.skin, font=f_sm, fill=(150, 200, 255))
    y += 22
    pd.text(
        (px0 + 20, y),
        f"{len(anchors) - len(unproven)}/{len(anchors)} proven against the core",
        font=f_sm,
        fill=(150, 200, 255),
    )
    y += 30
    for family, (colour, desc) in ANCHOR_GROUPS.items():
        n = counts.get(family, 0)
        if n == 0:
            continue
        pd.rectangle((px0 + 20, y + 3, px0 + 44, y + 21), fill=colour, outline=(255, 255, 255))
        pd.text((px0 + 54, y), f"{n:>3}  {desc}", font=f_sm, fill=(235, 235, 235))
        y += 23
    y += 12
    pd.text(
        (px0 + 20, y),
        "Each box straddles the edge the bet sits\n"
        "on, so it overlaps the cells it covers.\n"
        "Crosshair = the point automation clicks.",
        font=f_sm,
        fill=(150, 150, 160),
    )
    y += 70
    if unproven:
        pd.text((px0 + 20, y), "Not proven live yet", font=f_h2, fill=ORANGE)
        y += 26
        for line in _wrap(", ".join(sorted(unproven)), 46):
            pd.text((px0 + 20, y), line, font=f_sm, fill=(230, 205, 170))
            y += 19

    out_path = out_dir / "overlay_anchors.png"
    canvas.save(out_path)
    (out_dir / "overlay_anchors.json").write_text(
        json.dumps(
            {
                "layout_id": surface.layout_id,
                "counts": counts,
                "unproven": unproven,
                "anchors": {
                    name: entry.get("click_center_px") for name, entry in sorted(anchors.items())
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out_path


def _wrap(text: str, width: int) -> list[str]:
    out: list[str] = []
    line = ""
    for part in text.split(", "):
        candidate = f"{line}, {part}" if line else part
        if len(candidate) > width:
            out.append(line)
            line = part
        else:
            line = candidate
    if line:
        out.append(line)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Overlay mapped hitboxes on the roulette UI")
    p.add_argument("--ip", default=IP_DEFAULT)
    p.add_argument("--layout", default=DEFAULT_LAYOUT, choices=KNOWN_LAYOUTS)
    p.add_argument("--input", type=Path, help="Use an existing screenshot instead of capturing")
    p.add_argument(
        "--mode",
        default="all",
        help="ui | board | stats | verify | anchors | all | screen:<name> "
        "(a sub-screen, which needs an --input capture of that screen)",
    )
    args = p.parse_args(argv)

    surface = surface_for(args.layout)
    fixed = ("ui", "board", "stats", "verify", "anchors", "all")
    if args.mode not in fixed and not args.mode.startswith("screen:"):
        p.error(f"invalid mode {args.mode!r}: choose from {', '.join(fixed)} or screen:<name>")
    if args.mode.startswith("screen:"):
        screen = args.mode.split(":", 1)[1]
        if screen not in surface.overlays:
            p.error(
                f"{args.layout} has no {screen!r} sub-screen; "
                f"known: {', '.join(sorted(surface.overlays)) or 'none'}"
            )
    out_dir = out_dir_for(args.layout)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.input and args.input.is_file():
        base = args.input
    else:
        print(f"capturing {args.ip} ({args.layout}) ...", flush=True)
        base = _grab(args.ip, out_dir, (surface.client_w, surface.client_h))

    if args.mode == "verify":
        print(f"wrote {render_verify(base, layout_id=args.layout)}", flush=True)
        return 0
    if args.mode == "anchors":
        print(f"wrote {render_anchors(base, layout_id=args.layout)}", flush=True)
        return 0

    modes = ["ui", "board", "all"] if args.mode == "all" else [args.mode]
    for mode in modes:
        if mode == "stats" and not surface.stats:
            print(f"skip stats: {args.layout} has no mapped statistics panel", flush=True)
            continue
        print(f"wrote {render(base, mode=mode, layout_id=args.layout)}", flush=True)
    if args.mode == "all":
        print(f"wrote {render_anchors(base, layout_id=args.layout)}", flush=True)
        if verify_results_for(args.layout).is_file():
            print(f"wrote {render_verify(base, layout_id=args.layout)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
