"""
Drive and read the roulette GUI sliders (OPACIDAD) on a lab cabinet.

The Godot control is one wide ``TouchScreenButton`` running
``addons/WinSysAddons/DragScrollButton.cs``, which reports the picked X through
its ``SelectedXAmount`` signal to ``OpacitySlider._on_value_set``. Because the
value comes from the X coordinate, a plain tap on the track sets it — a drag is
supported (``use_drag=True``) but never required.

Verified on 10.0.0.90 (2026-07-24): taps at x=1120 and x=1150 parked the handle
ring at 1119 / 1149, and over-dragging clamped it at 1103 (0.0) and 1233 (1.0).
Moving the slider produced no visible change to the bars or cloth in the normal
square view, so treat the value as cosmetic state, not a gameplay control.

The control exists on both skins but in different places, so pass ``--layout`` to
match whichever the cabinet is showing.

CLI:
    python -m automation.roulette_slider --ip 10.0.0.90 --read
    python -m automation.roulette_slider --ip 10.0.0.90 --value 0.5
    python -m automation.roulette_slider --ip 10.0.0.90 --value 1.0 --drag
    python -m automation.roulette_slider --layout layout2 --read
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from automation.remote_input_agent import (
    build_input_agent_local,
    run_input_script_on_cabinet,
    stage_input_agent,
)
from automation.roulette_layout import ROULETTE_FOCUS_PROCESS
from automation.roulette_script import script_for_slider_set
from automation.roulette_surface import DEFAULT_LAYOUT, surface_for


def find_handle_x(
    screenshot: str | Path,
    *,
    name: str = "OPACITY_SLIDER",
    layout_id: str | None = None,
) -> float | None:
    """Handle-ring centre x in a client-sized screenshot, or None if unseen."""
    from PIL import Image  # local import: keeps the module usable without Pillow

    surface = surface_for(layout_id)
    spec = surface.sliders.get(name)
    if spec is None:
        raise KeyError(f"{name!r} is not a mapped slider; known: {tuple(surface.sliders)}")

    im = Image.open(screenshot).convert("L")
    if im.size[1] >= 2000:  # multi-monitor capture: client is the top-left frame
        im = im.crop((0, 0, surface.client_w, surface.client_h))
    px = im.load()
    x0 = spec["ring_x0"]
    x1 = min(spec["ring_x1"], im.size[0])
    rows = range(spec["ring_y0"], min(spec["ring_y1"], im.size[1]))
    threshold = spec["ring_min_luma"]
    cols = [x for x in range(x0, x1) if any(px[x, y] > threshold for y in rows)]
    if not cols:
        return None
    return (min(cols) + max(cols)) / 2.0


def read_slider_value(
    ip: str,
    *,
    name: str = "OPACITY_SLIDER",
    layout_id: str | None = None,
    screenshot: str | Path | None = None,
) -> dict[str, Any]:
    """Capture the cabinet screen (unless *screenshot* is given) and read the value."""
    surface = surface_for(layout_id)
    if name not in surface.sliders:
        raise KeyError(f"{name!r} is not a mapped slider; known: {tuple(surface.sliders)}")
    if screenshot is None:
        from network.screen_capture import capture_remote_screen

        shot = Path(tempfile.gettempdir()) / f"slider_{name.lower()}_{ip.replace('.', '_')}.jpg"
        ok, detail = capture_remote_screen(ip, str(shot))
        if not ok:
            return {"ok": False, "error": detail, "handle_x": None, "value": None}
        screenshot = shot
    handle_x = find_handle_x(screenshot, name=name, layout_id=layout_id)
    if handle_x is None:
        return {
            "ok": False,
            "error": "handle ring not found",
            "handle_x": None,
            "value": None,
            "screenshot": str(screenshot),
        }
    return {
        "ok": True,
        "error": "",
        "handle_x": handle_x,
        "value": round(surface.slider_px_to_value(name, handle_x), 4),
        "screenshot": str(screenshot),
    }


def set_slider(
    ip: str,
    value: float,
    *,
    name: str = "OPACITY_SLIDER",
    layout_id: str | None = None,
    use_drag: bool = False,
    from_value: float | None = None,
    verify: bool = True,
    tolerance_px: float = 4.0,
    agent: Any = None,
) -> dict[str, Any]:
    """
    Tap (or drag) *name* to *value* in 0.0..1.0 and, when *verify*, confirm the
    handle landed within *tolerance_px* of the requested position.

    *from_value* is where a drag grabs the handle (ignored for taps).
    """
    surface = surface_for(layout_id)
    script = script_for_slider_set(
        value, name=name, layout_id=layout_id, use_drag=use_drag, from_value=from_value
    )
    if agent is None:
        agent = stage_input_agent(
            ip=ip, local_exe=build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
        )
    run_input_script_on_cabinet(
        ip=ip, agent=agent, script=script, focus_process=ROULETTE_FOCUS_PROCESS, timeout=60
    )
    want_x, want_y = surface.slider_value_to_px(name, value)
    out: dict[str, Any] = {
        "ok": True,
        "layout_id": surface.layout_id,
        "slider": name,
        "requested_value": round(min(1.0, max(0.0, float(value))), 4),
        "target_px": {"x": want_x, "y": want_y},
        "gesture": "drag" if use_drag else "tap",
    }
    if not verify:
        return out
    reading = read_slider_value(ip, name=name, layout_id=layout_id)
    out["measured"] = reading
    if not reading["ok"]:
        out["ok"] = False
        return out
    out["error_px"] = round(abs(float(reading["handle_x"]) - want_x), 2)
    out["ok"] = out["error_px"] <= tolerance_px
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Set or read a roulette GUI slider")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--layout", default=DEFAULT_LAYOUT, choices=("layout1", "layout2"))
    p.add_argument("--slider", default="OPACITY_SLIDER")
    p.add_argument("--value", type=float, default=None, help="0.0 (min) .. 1.0 (max)")
    p.add_argument("--read", action="store_true", help="Only report the current value")
    p.add_argument("--drag", action="store_true", help="Grab the handle instead of tapping")
    p.add_argument(
        "--from-value",
        type=float,
        default=None,
        help="Where a drag grabs the handle (default: current park position 1.0)",
    )
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--geometry", action="store_true", help="Print the mapped travel and exit")
    args = p.parse_args(argv)

    if args.geometry:
        print(json.dumps(surface_for(args.layout).sliders, indent=2))
        return 0
    if args.read or args.value is None:
        print(
            json.dumps(
                read_slider_value(args.ip, name=args.slider, layout_id=args.layout),
                indent=2,
                default=str,
            )
        )
        return 0

    result = set_slider(
        args.ip,
        args.value,
        name=args.slider,
        layout_id=args.layout,
        use_drag=args.drag,
        from_value=args.from_value,
        verify=not args.no_verify,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
