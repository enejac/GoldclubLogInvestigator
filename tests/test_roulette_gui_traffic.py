import json
import tempfile
from pathlib import Path

from automation.roulette_gui_traffic import parse_godot_put_actions, parse_http_gui_dump, summarize_events
from automation.roulette_layout import OUTSIDE_SPOTS
from automation.roulette_layout_store import (
    CalibrationHit,
    load_calibration,
    record_target,
    resolve_target,
    spiral_offsets,
)
import automation.roulette_layout_store as store


def test_parse_http_gui_dump() -> None:
    sample = """
HTTPGUISNIFF mode=SNIFF
HTTP t=   120 wall=12:00:01.001 dir=C2S sport=51234 dport=8090 method=PUT status=- path=/api/action/0 body={"Action":"SetChip","Data":0}
HTTP t=   200 wall=12:00:01.080 dir=S2C sport=8090 dport=51234 method=- status=200 path=- body={"ok":true,"credit":100}
PACKETS 9
"""
    events = parse_http_gui_dump(sample)
    assert len(events) == 2
    assert events[0].method == "PUT"
    assert events[0].action_hint == "SetChip"
    assert events[1].status == "200"
    assert summarize_events(events)["SetChip"] == 1


def test_parse_godot_put_actions() -> None:
    lines = [
        "2026-07-24T07:56:01.064+00:00 INFO [:] Sending put action SetChip data 0",
        "2026-07-24T07:56:01.065+00:00 INFO [:] PLAYER0: _on_TouchZoneButTouchScreen_released",
    ]
    assert parse_godot_put_actions(lines) == [("SetChip", "0")]


def test_spiral_and_calibration_roundtrip() -> None:
    offs = spiral_offsets(max_step=1.0, step=0.5)
    assert offs[0] == (0.0, 0.0)
    assert (0.5, 0.0) in offs

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # Calibration is written to the store's writable folder, which is the repo
        # copy in a dev run; point both that and the folder it seeds itself from at a
        # scratch folder, so the test neither edits the tracked JSON nor inherits the
        # proven hitboxes (which outrank calibration when they exist).
        old_dir = store.layouts_dir
        old_bundled = store.bundled_layouts_dir
        old_cache = dict(store._CACHE)
        try:
            store.layouts_dir = lambda **_kw: tmp  # type: ignore[assignment]
            store.bundled_layouts_dir = lambda: tmp  # type: ignore[assignment]
            store._CACHE.clear()
            store._HITBOX_CACHE.clear()
            red = next(s for s in OUTSIDE_SPOTS if s.name == "RED")
            record_target(
                CalibrationHit(
                    name="RED",
                    x_pct=47.0,
                    y_pct=71.0,
                    verified=True,
                    credit_delta=-100000,
                    http_actions=("SetChip:0",),
                    note="test",
                ),
                layout_id="layout1",
            )
            resolved = resolve_target(red, "layout1")
            assert resolved.x_pct == 47.0
            assert resolved.y_pct == 71.0
            cal = load_calibration("layout1")
            assert cal["targets"]["RED"]["verified"] is True
            assert json.loads((tmp / "layout1.json").read_text(encoding="utf-8"))["layout_id"] == "layout1"
        finally:
            store.layouts_dir = old_dir  # type: ignore[assignment]
            store.bundled_layouts_dir = old_bundled  # type: ignore[assignment]
            store._CACHE.clear()
            store._HITBOX_CACHE.clear()
            store._CACHE.update(old_cache)
