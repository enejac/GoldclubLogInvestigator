"""
One roulette skin's clickable surface, so tools can target any layout.

Every skin draws the same American double-zero game with a different geometry:
layout1 (``futura_doublezero``) puts the dozens above the numbers and the spin
disc at top centre, layout2 (``futura_doublezeroCrycle``) puts the dozens below
the numbers and START in the bottom-right. A :class:`Surface` holds one skin's
measured boxes plus the behaviour flags that decide what may be automated, and
:func:`surface_for` hands the right one to the mapper, overlay and verifier.

Boxes are ``(x0, y0, x1, y1)`` inclusive client pixels measured from a live
capture at the surface's own ``client_w`` x ``client_h``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Box = tuple[int, int, int, int]

DEFAULT_LAYOUT = "layout1"
KNOWN_LAYOUTS = ("layout1", "layout2", "slot")

# layout id -> module that registers it, imported on first use so neither skin
# module has to be loaded before it is actually needed.
_PROVIDERS = {
    "layout1": "automation.roulette_ui_areas",
    "layout2": "automation.roulette_layout2_areas",
    "slot": "automation.roulette_slot_areas",
}

_SURFACES: dict[str, "Surface"] = {}


def pct_area_to_px(
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    *,
    client_w: int = 1920,
    client_h: int = 1080,
) -> tuple[int, int, int, int]:
    """Convert pct bounds to an (x, y, width, height) pixel box."""
    x = int(round(client_w * x_min / 100.0))
    y = int(round(client_h * y_min / 100.0))
    w = max(1, int(round(client_w * (x_max - x_min) / 100.0)))
    h = max(1, int(round(client_h * (y_max - y_min) / 100.0)))
    return x, y, w, h


def area_pct_dict(x_min: float, y_min: float, x_max: float, y_max: float) -> dict[str, float]:
    return {
        "x_min": round(x_min, 3),
        "y_min": round(y_min, 3),
        "x_max": round(x_max, 3),
        "y_max": round(y_max, 3),
    }


@dataclass(frozen=True)
class Surface:
    """Measured clickable geometry and automation rules for one roulette skin."""

    layout_id: str
    skin: str
    description: str = ""
    client_w: int = 1920
    client_h: int = 1080
    numbers: dict[str, Box] = field(default_factory=dict)
    outside: dict[str, Box] = field(default_factory=dict)
    ui: dict[str, Box] = field(default_factory=dict)
    stats: dict[str, Box] = field(default_factory=dict)
    # Sub-screens that only exist once something has been clicked (language
    # chooser, help, paytable...). Keyed by screen name; the statistics panel is
    # folded in under "statistics" so every off-base control is reachable the
    # same way.
    overlays: dict[str, dict[str, Box]] = field(default_factory=dict)
    sliders: dict[str, dict[str, int]] = field(default_factory=dict)
    forbidden: tuple[str, ...] = ()
    info_only: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    # Boxes whose geometry is measured but whose effect is not yet proven live.
    unproven: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        overlays = dict(self.overlays)
        if self.stats:
            overlays = {"statistics": dict(self.stats), **overlays}
        object.__setattr__(self, "overlays", overlays)

        bet = {name: self._pct(box) for name, box in {**self.numbers, **self.outside}.items()}
        ui = {name: self._pct(box) for name, box in self.ui.items()}
        stats = {name: self._pct(box) for name, box in self.stats.items()}
        extra = {
            name: self._pct(box)
            for screen, boxes in overlays.items()
            if screen != "statistics"
            for name, box in boxes.items()
        }
        object.__setattr__(self, "bet_areas_pct", bet)
        object.__setattr__(self, "ui_areas_pct", ui)
        object.__setattr__(self, "stats_areas_pct", stats)
        object.__setattr__(self, "overlay_areas_pct", extra)
        object.__setattr__(self, "all_areas_pct", {**bet, **ui, **stats, **extra})

    def _pct(self, box: Box) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = box
        return (
            100.0 * x0 / self.client_w,
            100.0 * y0 / self.client_h,
            100.0 * x1 / self.client_w,
            100.0 * y1 / self.client_h,
        )

    # --- geometry lookups -------------------------------------------------
    @property
    def bet_areas_px(self) -> dict[str, Box]:
        return {**self.numbers, **self.outside}

    @property
    def all_areas_px(self) -> dict[str, Box]:
        overlays = {name: box for boxes in self.overlays.values() for name, box in boxes.items()}
        return {**self.numbers, **self.outside, **self.ui, **overlays}

    def area_of(self, button_id: str) -> tuple[float, float, float, float] | None:
        return self.all_areas_pct.get(button_id)

    def overlay_of(self, button_id: str) -> str | None:
        """Which sub-screen a control lives on, or None when it is on the base screen."""
        for screen, boxes in self.overlays.items():
            if button_id in boxes:
                return screen
        return None

    # --- sliders ----------------------------------------------------------
    def slider_value_to_px(self, name: str, value: float) -> tuple[int, int]:
        """Screen point that sets *name* to *value* (0.0 = min, 1.0 = max)."""
        spec = self.sliders.get(name)
        if spec is None:
            raise KeyError(f"{name!r} is not a mapped slider; known: {tuple(self.sliders)}")
        v = min(1.0, max(0.0, float(value)))
        lo, hi = spec["handle_min_x"], spec["handle_max_x"]
        return int(round(lo + (hi - lo) * v)), spec["track_y"]

    def slider_px_to_value(self, name: str, x: float) -> float:
        """
        Inverse of :meth:`slider_value_to_px` — read a value off a handle x.

        The handle is a wide ring whose detected centre lands within a pixel of
        the tap that set it (an even-width ring reads half a pixel off), so the
        outermost pixel of travel counts as the end value rather than 0.996.
        """
        spec = self.sliders.get(name)
        if spec is None:
            raise KeyError(f"{name!r} is not a mapped slider; known: {tuple(self.sliders)}")
        lo, hi = spec["handle_min_x"], spec["handle_max_x"]
        if hi == lo:
            return 0.0
        if x <= lo + 1:
            return 0.0
        if x >= hi - 1:
            return 1.0
        return min(1.0, max(0.0, (float(x) - lo) / (hi - lo)))

    def slider_meta(self, name: str) -> dict[str, Any] | None:
        """Slider geometry for a hitbox entry (px + pct, both axes)."""
        spec = self.sliders.get(name)
        if spec is None:
            return None
        lo, hi, track_y = spec["handle_min_x"], spec["handle_max_x"], spec["track_y"]
        return {
            "control": "slider",
            "axis": "x",
            "value_min": 0.0,
            "value_max": 1.0,
            "set_by": "tap",
            "handle_min_px": {"x": lo, "y": track_y},
            "handle_max_px": {"x": hi, "y": track_y},
            "handle_min_pct": {
                "x_pct": round(100.0 * lo / self.client_w, 3),
                "y_pct": round(100.0 * track_y / self.client_h, 3),
            },
            "handle_max_pct": {
                "x_pct": round(100.0 * hi / self.client_w, 3),
                "y_pct": round(100.0 * track_y / self.client_h, 3),
            },
            "travel_px": hi - lo,
        }

    # --- hitbox entries ---------------------------------------------------
    def kind_of(self, button_id: str) -> str:
        if button_id in self.sliders:
            return "slider"
        if button_id in self.info_only:
            return "readout"
        if button_id in self.stats:
            return "stats_panel"
        if self.overlay_of(button_id):
            return "overlay"
        if button_id in self.ui:
            return "ui"
        return "bet"

    def automation_of(self, button_id: str) -> str | None:
        if button_id in self.forbidden:
            return "forbidden"
        if button_id in self.info_only:
            return "read_only"
        if button_id in self.sliders:
            return "slider"
        if button_id in self.avoid:
            return "avoid"
        return None

    def build_hitbox_fields(
        self,
        button_id: str,
        *,
        client_w: int | None = None,
        client_h: int | None = None,
        kind: str | None = None,
        note: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Build x/y/width/height + click_center + click_area_pct for a mapped id."""
        bounds = self.all_areas_pct.get(button_id)
        if bounds is None:
            return None
        cw = client_w or self.client_w
        ch = client_h or self.client_h
        x_min, y_min, x_max, y_max = bounds
        x, y, w, h = pct_area_to_px(x_min, y_min, x_max, y_max, client_w=cw, client_h=ch)
        cx, cy = x + w // 2, y + h // 2
        out: dict[str, Any] = {
            "x": x,
            "y": y,
            "width": w,
            "height": h,
            "click_center_px": {"x": cx, "y": cy},
            "click_center_pct": {
                "x_pct": round(100.0 * cx / cw, 3),
                "y_pct": round(100.0 * cy / ch, 3),
            },
            "click_area_pct": area_pct_dict(x_min, y_min, x_max, y_max),
            "kind": kind or self.kind_of(button_id),
            "client": {"width": cw, "height": ch},
        }
        automation = self.automation_of(button_id)
        if automation:
            out["automation"] = automation
        if button_id in self.sliders:
            # Centre of the box is not a useful click point for a slider; aim at
            # the value the handle is parked at by default (full opacity).
            sx, sy = self.slider_value_to_px(button_id, 1.0)
            out["click_center_px"] = {"x": sx, "y": sy}
            out["click_center_pct"] = {
                "x_pct": round(100.0 * sx / cw, 3),
                "y_pct": round(100.0 * sy / ch, 3),
            }
            out["slider"] = self.slider_meta(button_id)
        screen = self.overlay_of(button_id)
        if screen:
            out["overlay"] = screen
        if button_id in self.unproven:
            out["proof"] = "geometry measured; effect not verified live yet"
        if note:
            out["note"] = note
        if extra:
            out.update(extra)
        return out


def register(surface: Surface) -> Surface:
    _SURFACES[surface.layout_id] = surface
    return surface


def surface_for(layout_id: str | None = None) -> Surface:
    """Return the measured surface for *layout_id*, importing its module once."""
    lid = (layout_id or DEFAULT_LAYOUT).strip().lower()
    if lid not in KNOWN_LAYOUTS:
        raise ValueError(f"unsupported layout_id {layout_id!r} (use {'|'.join(KNOWN_LAYOUTS)})")
    if lid not in _SURFACES:
        __import__(_PROVIDERS[lid])
    return _SURFACES[lid]


def registered_layouts() -> tuple[str, ...]:
    return tuple(_SURFACES)


__all__ = [
    "Box",
    "DEFAULT_LAYOUT",
    "KNOWN_LAYOUTS",
    "Surface",
    "area_pct_dict",
    "pct_area_to_px",
    "register",
    "registered_layouts",
    "surface_for",
]
