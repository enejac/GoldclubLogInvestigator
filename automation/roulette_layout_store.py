"""
Persistent click-target calibration for roulette layouts.

layout1 = futura_doublezero (Alegro default: dozens above the grid, spin disc top)
layout2 = futura_doublezeroCrycle (dozens below the grid, START bottom-right)
slot    = OneHand Slot Roulette / RouletteGame (European cloth, 1920x3240 triple-stack)

Skins' measured geometry lives in :mod:`automation.roulette_surface`; this
module layers proven hitboxes (``{layout}_hitboxes.json``) and per-target
calibration (``{layout}.json``) on top of it.

Those two JSON files are the part of the map an operator can change without a
new build, so they are read from and written to a **writable** folder next to
the exe rather than the read-only PyInstaller bundle. Files are seeded from the
bundle the first time they are needed, which keeps a fresh install working and
lets a later mapping run replace them in place.
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app_paths import app_install_dir, app_writable_dir, is_network_path
from automation.roulette_layout import ClickTarget
from automation.roulette_surface import (
    KNOWN_LAYOUTS,
    area_pct_dict,
    pct_area_to_px,
    surface_for,
)

_LOCK = threading.RLock()
_ACTIVE_LAYOUT = "layout1"
_CACHE: dict[str, dict[str, Any]] = {}
# path -> (mtime_ns, size, parsed). Hitboxes are re-read per click resolve, and
# the layout2 registry is ~12k lines, so a stat-keyed cache keeps sweeps quick
# while still noticing a mapping run that rewrote the file.
_HITBOX_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}


def bundled_layouts_dir() -> Path:
    """Layout JSON shipped with the code (read-only ``_MEIPASS`` when frozen)."""
    return Path(__file__).resolve().parent / "layouts"


def layouts_dir(*, mkdir: bool = True) -> Path:
    """
    Writable folder holding hitbox + calibration JSON.

    Frozen on a local disk: ``automation\\layouts`` beside ``LogInvestigator.exe``
    (the PyInstaller bundle is wiped on exit). Frozen on a UNC share: under the
    local writable root so hitbox writes do not hold the stick share open.
    From source: the repo ``automation\\layouts`` folder.
    """
    if getattr(sys, "frozen", False):
        install = app_install_dir()
        if is_network_path(install):
            path = app_writable_dir(mkdir=mkdir) / "automation" / "layouts"
        else:
            path = install / "automation" / "layouts"
    else:
        path = bundled_layouts_dir()
    if mkdir:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _seeded(name: str) -> Path:
    """Path to *name* in the writable folder, copied from the bundle if absent."""
    live = layouts_dir() / name
    if not live.is_file():
        src = bundled_layouts_dir() / name
        try:
            if src.is_file() and src.resolve() != live.resolve():
                live.write_bytes(src.read_bytes())
        except OSError:
            return live
    return live


def layout_path(layout_id: str) -> Path:
    lid = (layout_id or "layout1").strip().lower()
    return _seeded(f"{lid}.json")


def hitboxes_path(layout_id: str | None = None) -> Path:
    """Writable path of a skin's proven-hitbox registry."""
    lid = (layout_id or _ACTIVE_LAYOUT).strip().lower()
    return _seeded(f"{lid}_hitboxes.json")


def set_active_layout(layout_id: str) -> str:
    global _ACTIVE_LAYOUT
    lid = (layout_id or "layout1").strip().lower()
    if lid not in KNOWN_LAYOUTS:
        raise ValueError(
            f"unsupported layout_id {layout_id!r} (use {'|'.join(KNOWN_LAYOUTS)})"
        )
    with _LOCK:
        _ACTIVE_LAYOUT = lid
    return lid


def active_layout_id() -> str:
    return _ACTIVE_LAYOUT


def load_calibration(layout_id: str | None = None) -> dict[str, Any]:
    lid = (layout_id or _ACTIVE_LAYOUT).strip().lower()
    path = layout_path(lid)
    with _LOCK:
        if lid in _CACHE:
            return _CACHE[lid]
        if not path.is_file():
            data = {
                "layout_id": lid,
                "skin": {
                    "layout1": "futura_doublezero",
                    "layout2": "futura_doublezeroCrycle",
                    "slot": "RouletteGame",
                }.get(lid, lid),
                "global_offset": {"dx_pct": 0.0, "dy_pct": 0.0},
                "targets": {},
            }
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
        if "targets" not in data or not isinstance(data["targets"], dict):
            data["targets"] = {}
        if "global_offset" not in data:
            data["global_offset"] = {"dx_pct": 0.0, "dy_pct": 0.0}
        _CACHE[lid] = data
        return data


def save_calibration(data: dict[str, Any], layout_id: str | None = None) -> Path:
    lid = (layout_id or data.get("layout_id") or _ACTIVE_LAYOUT).strip().lower()
    data = dict(data)
    data["layout_id"] = lid
    path = layout_path(lid)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=True) + "\n"
    path.write_text(text, encoding="utf-8")
    with _LOCK:
        _CACHE[lid] = data
    return path


def load_hitboxes(layout_id: str | None = None) -> dict[str, Any]:
    """Pixel hitboxes proven via middleware (``{layout}_hitboxes.json``)."""
    lid = (layout_id or _ACTIVE_LAYOUT).strip().lower()
    path = hitboxes_path(lid)
    try:
        st = path.stat()
    except OSError:
        return {"layout_id": lid, "buttons": {}}
    key = str(path)
    with _LOCK:
        cached = _HITBOX_CACHE.get(key)
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            return cached[2]
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    with _LOCK:
        _HITBOX_CACHE[key] = (st.st_mtime_ns, st.st_size, data)
    return data


def invalidate_hitbox_cache(path: Path | str | None = None) -> None:
    """Forget a cached registry after something else rewrote the file."""
    with _LOCK:
        if path is None:
            _HITBOX_CACHE.clear()
        else:
            _HITBOX_CACHE.pop(str(path), None)


def save_hitboxes(data: dict[str, Any], layout_id: str | None = None) -> Path:
    """Write a skin's hitbox registry to the writable folder and refresh the cache."""
    lid = (layout_id or data.get("layout_id") or _ACTIVE_LAYOUT).strip().lower()
    path = hitboxes_path(lid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    with _LOCK:
        _HITBOX_CACHE.pop(str(path), None)
    return path


@dataclass(frozen=True, slots=True)
class HitboxArea:
    """Axis-aligned clickable region in client pixels."""

    name: str
    x: int
    y: int
    width: int
    height: int
    client_width: int = 1920
    client_height: int = 1080

    @property
    def click_area_pct(self) -> dict[str, float]:
        cw, ch = self.client_width, self.client_height
        return area_pct_dict(
            100.0 * self.x / cw,
            100.0 * self.y / ch,
            100.0 * (self.x + self.width) / cw,
            100.0 * (self.y + self.height) / ch,
        )

    def center_target(self) -> ClickTarget:
        cx = self.x + self.width // 2
        cy = self.y + self.height // 2
        return ClickTarget(
            self.name,
            100.0 * cx / self.client_width,
            100.0 * cy / self.client_height,
        )

    def contains_pct(self, x_pct: float, y_pct: float) -> bool:
        a = self.click_area_pct
        return a["x_min"] <= x_pct <= a["x_max"] and a["y_min"] <= y_pct <= a["y_max"]


def parse_hitbox_area(entry: dict[str, Any], layout_id: str | None = None) -> HitboxArea | None:
    """Parse x/y/width/height or click_area_pct into a HitboxArea."""
    if not isinstance(entry, dict):
        return None
    client = entry.get("client") or {}
    cw = int(client.get("width") or 1920)
    ch = int(client.get("height") or 1080)
    name = str(entry.get("id") or "")
    if all(k in entry for k in ("x", "y", "width", "height")):
        w, h = int(entry["width"]), int(entry["height"])
        if w > 8 or h > 8:
            return HitboxArea(
                name, int(entry["x"]), int(entry["y"]), w, h, cw, ch
            )
    area = entry.get("click_area_pct")
    if isinstance(area, dict) and all(k in area for k in ("x_min", "y_min", "x_max", "y_max")):
        x, y, w, h = pct_area_to_px(
            float(area["x_min"]),
            float(area["y_min"]),
            float(area["x_max"]),
            float(area["y_max"]),
            client_w=cw,
            client_h=ch,
        )
        return HitboxArea(name, x, y, w, h, cw, ch)
    bounds = surface_for(layout_id or _ACTIVE_LAYOUT).area_of(name)
    if bounds:
        x, y, w, h = pct_area_to_px(*bounds, client_w=cw, client_h=ch)
        return HitboxArea(name, x, y, w, h, cw, ch)
    return None


def resolve_hitbox_area(
    button_id: str, layout_id: str | None = None, *, allow_geometry: bool = True
) -> HitboxArea | None:
    buttons = (load_hitboxes(layout_id).get("buttons") or {})
    entry = buttons.get(button_id)
    if not isinstance(entry, dict):
        if not allow_geometry:
            return None
        # Not proven yet: fall back to the skin's measured geometry.
        surface = surface_for(layout_id or _ACTIVE_LAYOUT)
        bounds = surface.area_of(button_id)
        if bounds is None:
            return None
        x, y, w, h = pct_area_to_px(
            *bounds, client_w=surface.client_w, client_h=surface.client_h
        )
        return HitboxArea(button_id, x, y, w, h, surface.client_w, surface.client_h)
    return parse_hitbox_area(entry, layout_id)


def resolve_hitbox_center(
    button_id: str, layout_id: str | None = None, *, allow_geometry: bool = True
) -> ClickTarget | None:
    """
    Return the click point for a mapped button.

    The stored ``click_center_pct`` wins because it may be a live-verified point
    that is deliberately off the geometric centre; the area centre is the
    fallback for entries that only carry bounds. With *allow_geometry* off, only
    a real registry entry answers, so callers can tell a mapped hitbox apart
    from the skin's raw measured geometry.
    """
    buttons = (load_hitboxes(layout_id).get("buttons") or {})
    entry = buttons.get(button_id)
    if isinstance(entry, dict):
        pct = entry.get("click_center_pct") or {}
        if "x_pct" in pct and "y_pct" in pct:
            return ClickTarget(button_id, float(pct["x_pct"]), float(pct["y_pct"]))
    area = resolve_hitbox_area(button_id, layout_id, allow_geometry=allow_geometry)
    if area is not None:
        return area.center_target()
    return None


def resolve_target(base: ClickTarget, layout_id: str | None = None) -> ClickTarget:
    """Apply calibration override / global offset to a base geometry target."""
    # A registry hitbox is middleware-proven, so it outranks the calibration file;
    # raw surface geometry does not, and must not shadow a recorded hit.
    hb = resolve_hitbox_center(base.name, layout_id, allow_geometry=False)
    if hb is not None:
        return hb
    cal = load_calibration(layout_id)
    g = cal.get("global_offset") or {}
    dx = float(g.get("dx_pct", 0.0) or 0.0)
    dy = float(g.get("dy_pct", 0.0) or 0.0)
    entry = (cal.get("targets") or {}).get(base.name)
    if isinstance(entry, dict) and "x_pct" in entry and "y_pct" in entry:
        return ClickTarget(
            base.name,
            float(entry["x_pct"]) + dx,
            float(entry["y_pct"]) + dy,
        )
    return ClickTarget(base.name, base.x_pct + dx, base.y_pct + dy)


def resolve_many(targets: list[ClickTarget], layout_id: str | None = None) -> list[ClickTarget]:
    return [resolve_target(t, layout_id) for t in targets]


@dataclass(frozen=True, slots=True)
class CalibrationHit:
    name: str
    x_pct: float
    y_pct: float
    verified: bool
    credit_delta: int | None = None
    http_actions: tuple[str, ...] = ()
    note: str = ""


def record_target(
    hit: CalibrationHit,
    *,
    layout_id: str | None = None,
) -> dict[str, Any]:
    """Upsert a verified (or failed) target into the layout calibration file."""
    lid = layout_id or _ACTIVE_LAYOUT
    cal = dict(load_calibration(lid))
    targets = dict(cal.get("targets") or {})
    prev = targets.get(hit.name) if isinstance(targets.get(hit.name), dict) else {}
    hits = int(prev.get("hits", 0) or 0)
    misses = int(prev.get("misses", 0) or 0)
    if hit.verified:
        hits += 1
    else:
        misses += 1
    targets[hit.name] = {
        "x_pct": round(hit.x_pct, 3),
        "y_pct": round(hit.y_pct, 3),
        "verified": bool(hit.verified),
        "hits": hits,
        "misses": misses,
        "credit_delta": hit.credit_delta,
        "http_actions": list(hit.http_actions),
        "note": hit.note,
    }
    cal["targets"] = targets
    save_calibration(cal, lid)
    return targets[hit.name]


def spiral_offsets(max_step: float = 2.5, step: float = 0.5) -> list[tuple[float, float]]:
    """Search offsets around a miss, including (0,0) first."""
    out: list[tuple[float, float]] = [(0.0, 0.0)]
    n = int(round(max_step / step))
    for ring in range(1, n + 1):
        r = ring * step
        for dx, dy in (
            (r, 0.0),
            (-r, 0.0),
            (0.0, r),
            (0.0, -r),
            (r, r),
            (r, -r),
            (-r, r),
            (-r, -r),
        ):
            out.append((dx, dy))
    return out
