"""
Fast pixel hitbox mapper for Layout1.

Sweeps ROI strips during open betting windows. Each pixel is proven via
middleware ``PlayerDataBets`` (BetType/Id). Emits::

    {"button": {"id": "straightup_red_1", "x": .., "y": .., "width": .., "height": ..}}
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from automation.remote_exec import winrm_run_script
from automation.roulette_layout import CANCEL_ALL_BUTTON, DEFAULT_CHIP
from automation.roulette_layout_store import resolve_target
from automation.roulette_runner import _CLOSED_RE, _latest_file, wait_for_log_marker

REPO = Path(__file__).resolve().parents[1]
CS_SRC = REPO / "probes" / "RouletteHitboxScan.cs"
LAUNCHER_SRC = REPO / "automation" / "run_hitbox_scan_interactive.ps1"


def _merge_hits(parts: list[dict[str, Any]]) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    wrong: list[dict[str, Any]] = []
    client = {"width": 1920, "height": 1080}
    step = 5
    for p in parts:
        client = p.get("client") or client
        step = int(p.get("step_px") or step)
        hits.extend(p.get("hits") or [])
        wrong.extend(p.get("wrong") or [])
    # Any pixel that produced a wrong bet is NOT Fields/1 — drop conflicting hits.
    wrong_keys = {(int(w["x"]), int(w["y"])) for w in wrong if "x" in w and "y" in w}
    # unique by x,y
    seen: set[tuple[int, int]] = set()
    uniq_hits: list[dict[str, Any]] = []
    for h in hits:
        key = (int(h["x"]), int(h["y"]))
        if key in seen or key in wrong_keys:
            continue
        seen.add(key)
        uniq_hits.append(h)
    bx = by = bw = bh = 0
    if uniq_hits:
        xs = [int(h["x"]) for h in uniq_hits]
        ys = [int(h["y"]) for h in uniq_hits]
        pad = max(1, step // 2)
        bx = max(0, min(xs) - pad)
        by = max(0, min(ys) - pad)
        bw = min(int(client["width"]) - 1, max(xs) + pad) - bx + 1
        bh = min(int(client["height"]) - 1, max(ys) + pad) - by + 1
    return {
        "client": client,
        "step_px": step,
        "hits": uniq_hits,
        "wrong": wrong,
        "hit_count": len(uniq_hits),
        "wrong_count": len(wrong),
        "button": {"x": bx, "y": by, "width": max(0, bw), "height": max(0, bh)},
    }


def _run_one_strip(
    *,
    ip: str,
    expect_type: str,
    expect_id: str,
    roi: tuple[float, float, float, float],
    step_px: int,
    max_points: int,
    duration_ms: int,
    out_name: str,
) -> dict[str, Any]:
    chip = resolve_target(DEFAULT_CHIP)
    cancel = resolve_target(CANCEL_ALL_BUTTON)
    x0, y0, x1, y1 = roi

    unc_dir = Path(rf"\\{ip}\c$\Windows\Temp\investigator_hitbox")
    unc_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CS_SRC, unc_dir / "RouletteHitboxScan.cs")
    raw = LAUNCHER_SRC.read_bytes()
    if b"\x00" in raw[:4]:
        raw = raw.decode("utf-16").encode("utf-8")
    (unc_dir / "run_hitbox_scan_interactive.ps1").write_bytes(raw)

    remote_dir = r"C:\Windows\Temp\investigator_hitbox"
    out_remote = rf"{remote_dir}\{out_name}"
    err_remote = rf"{remote_dir}\scan_err.txt"
    exe_remote = rf"{remote_dir}\RouletteHitboxScan.exe"
    src_remote = rf"{remote_dir}\RouletteHitboxScan.cs"

    # Pre-compile via WinRM (non-interactive is fine for csc).
    compile_ps = f"""
$ErrorActionPreference = 'Stop'
$csc = 'C:\\Windows\\Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe'
$exe = '{exe_remote}'
$src = '{src_remote}'
if (-not (Test-Path $exe) -or (Get-Item $src).LastWriteTime -gt (Get-Item $exe -EA SilentlyContinue).LastWriteTime) {{
  & $csc /nologo /platform:x64 /optimize+ /out:$exe $src
}}
if (-not (Test-Path $exe)) {{ throw 'compile failed' }}
'COMPILE_OK'
"""
    from automation.remote_exec import winrm_run_inline

    cr = winrm_run_inline(ip=ip, script=compile_ps, timeout=60)
    if cr.returncode != 0 and "COMPILE_OK" not in (cr.stdout or ""):
        # still try if exe exists
        if not (unc_dir / "RouletteHitboxScan.exe").is_file():
            raise RuntimeError(f"compile failed: {cr.stdout}\n{cr.stderr}")

    arg_line = " ".join(
        [
            out_remote,
            expect_type,
            expect_id,
            f"{chip.x_pct}",
            f"{chip.y_pct}",
            f"{cancel.x_pct}",
            f"{cancel.y_pct}",
            f"{x0}",
            f"{y0}",
            f"{x1}",
            f"{y1}",
            str(step_px),
            str(max_points),
            "godot",
            str(duration_ms),
        ]
    )

    roulette = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta Roulette"))
    if roulette is None:
        raise FileNotFoundError("ruleta Roulette log missing")
    print(f"  strip ROI={roi} waiting closed...", flush=True)
    off = roulette.stat().st_size
    off, ok, detail = wait_for_log_marker(
        roulette, start_offset=off, pattern=_CLOSED_RE, timeout_sec=90
    )
    if not ok:
        raise TimeoutError(detail)
    print("  closed - launch in 5.5s", flush=True)
    time.sleep(5.5)

    r = winrm_run_script(
        ip=ip,
        remote_script_path=rf"{remote_dir}\run_hitbox_scan_interactive.ps1",
        script_args=[
            exe_remote,
            arg_line,
            out_remote,
            err_remote,
            "110",
        ],
        timeout=180,
    )
    out_unc = unc_dir / out_name
    if not out_unc.is_file():
        err = ""
        err_p = unc_dir / "scan_err.txt"
        if err_p.is_file():
            err = err_p.read_text(encoding="utf-8", errors="replace")[:800]
        raise RuntimeError(f"no scan output: winrm={r.stdout} {r.stderr} err={err}")

    return json.loads(out_unc.read_text(encoding="utf-8-sig"))


def _pct_roi_from_px(
    client: dict[str, Any], x0: int, y0: int, x1: int, y1: int
) -> tuple[float, float, float, float]:
    cw = max(1, int(client.get("width") or 1920))
    ch = max(1, int(client.get("height") or 1080))
    return (
        100.0 * x0 / cw,
        100.0 * y0 / ch,
        100.0 * x1 / cw,
        100.0 * y1 / ch,
    )


def _write_result(
    *,
    button_id: str,
    number: str,
    merged: dict[str, Any],
    parts: list[dict[str, Any]],
    stamp: str,
    out_dir: Path,
) -> dict[str, Any]:
    btn = {
        "id": button_id,
        "x": merged["button"]["x"],
        "y": merged["button"]["y"],
        "width": merged["button"]["width"],
        "height": merged["button"]["height"],
    }
    cw = int(merged["client"]["width"])
    ch = int(merged["client"]["height"])
    cx = btn["x"] + btn["width"] // 2
    cy = btn["y"] + btn["height"] // 2
    result = {
        "layout_id": "layout1",
        "chip": "chip_1",
        "expect": {"BetType": "Fields", "Id": str(number)},
        "client": merged["client"],
        "step_px": merged["step_px"],
        "hit_count": merged["hit_count"],
        "wrong_count": merged["wrong_count"],
        "button": btn,
        "click_center_px": {"x": cx, "y": cy},
        "click_center_pct": {
            "x_pct": round(100.0 * cx / cw, 3),
            "y_pct": round(100.0 * cy / ch, 3),
        },
        "hits": merged["hits"],
        "wrong_sample": (merged["wrong"] or [])[:40],
        "strips": [
            {
                "hit_count": p.get("hit_count"),
                "wrong_count": p.get("wrong_count"),
                "probed": p.get("probed"),
                "button": p.get("button"),
            }
            for p in parts
        ],
    }
    local = out_dir / f"{button_id}_{stamp}.json"
    local.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    registry = REPO / "automation" / "layouts" / "layout1_hitboxes.json"
    reg: dict[str, Any] = {"layout_id": "layout1", "buttons": {}}
    if registry.is_file():
        reg = json.loads(registry.read_text(encoding="utf-8-sig"))
        reg.setdefault("buttons", {})
    place_bet = ["1"] if str(number) == "1" else [str(number)]
    try:
        from automation.roulette_bet_catalog import place_bet_for_button

        pb = place_bet_for_button(button_id)
        if pb:
            place_bet = pb
    except Exception:
        pass
    reg["buttons"][button_id] = {
        **btn,
        "expect": result["expect"],
        "place_bet": place_bet,
        "chip": "chip_1",
        "chip_index": 0,
        "clear": "CancelAllBets",
        "hit_count": result["hit_count"],
        "step_px": result["step_px"],
        "click_center_px": result["click_center_px"],
        "click_center_pct": result["click_center_pct"],
        "source": str(local).replace("\\", "/"),
        "client": result["client"],
        "note": (
            "Pixel hitbox for cloth click. Clear/select via API CancelAllBets+SetChip "
            "(docs/action-endpoint-commands.md). place_bet is middleware PlaceBet Data."
        ),
    }
    registry.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "button": btn,
                "hit_count": result["hit_count"],
                "click_center_pct": result["click_center_pct"],
            },
            indent=2,
        ),
        flush=True,
    )
    print(f"wrote {local}", flush=True)
    return result


def map_straight_hitbox(
    *,
    ip: str = "10.0.0.90",
    number: str = "1",
    button_id: str = "straightup_red_1",
    step_px: int = 3,
    seed_path: str | None = None,
) -> dict[str, Any]:
    """
    Map Fields/{number} (Alegro straight-up) by scanning ROI strips + edge refine.
    """
    # Seeded from prior Fields/1 hits: x~298-374 y~637-649 (client 1920x1080).
    # Expand outward until Fields/1 fails (splits/corners/other fields).
    strips: list[tuple[float, float, float, float]] = [
        (15.3, 58.6, 19.7, 60.5),  # upper band of known cell
        (15.3, 60.3, 19.7, 62.2),  # mid/lower known cell
        (14.8, 57.8, 20.2, 59.0),  # expand up (toward split 1+2)
        (14.8, 61.8, 20.2, 64.0),  # expand down
        (14.2, 58.8, 15.6, 62.0),  # expand left
        (19.4, 58.8, 20.8, 62.0),  # expand right
    ]
    parts: list[dict[str, Any]] = []
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = REPO / "_tmp_logs" / "hitboxes"
    out_dir.mkdir(parents=True, exist_ok=True)

    if seed_path:
        seed = json.loads(Path(seed_path).read_text(encoding="utf-8-sig"))
        parts.append(seed)
        print(f"seeded {len(seed.get('hits') or [])} hits from {seed_path}", flush=True)

    for i, roi in enumerate(strips, start=1):
        print(f"=== strip {i}/{len(strips)} {roi} ===", flush=True)
        part = _run_one_strip(
            ip=ip,
            expect_type="Fields",
            expect_id=str(number),
            roi=roi,
            step_px=step_px,
            max_points=180,
            duration_ms=17000,
            out_name=f"strip_{button_id}_{stamp}_{i}.json",
        )
        # keep local copy
        (out_dir / f"strip_{button_id}_{stamp}_{i}.json").write_text(
            json.dumps(part, indent=2) + "\n", encoding="utf-8"
        )
        parts.append(part)
        print(
            f"  strip hits={part.get('hit_count')} wrong={part.get('wrong_count')} "
            f"probed={part.get('probed')}",
            flush=True,
        )

    merged = _merge_hits(parts)
    return _write_result(
        button_id=button_id,
        number=number,
        merged=merged,
        parts=parts,
        stamp=stamp,
        out_dir=out_dir,
    )


def refine_straight_hitbox(
    *,
    ip: str = "10.0.0.90",
    number: str = "1",
    button_id: str = "straightup_red_1",
    seed_path: str,
    step_px: int = 2,
    margin_px: int = 18,
) -> dict[str, Any]:
    """Expand edges around an existing hit cloud until Fields/{n} stops matching."""
    seed = json.loads(Path(seed_path).read_text(encoding="utf-8-sig"))
    hits = seed.get("hits") or []
    if not hits:
        raise RuntimeError(f"no hits in seed {seed_path}")
    client = seed.get("client") or {"width": 1920, "height": 1080}
    xs = [int(h["x"]) for h in hits]
    ys = [int(h["y"]) for h in hits]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    # Four edge bands + densify interior at finer step
    bands = [
        ("above", min_x - 4, min_y - margin_px, max_x + 4, min_y + 2),
        ("below", min_x - 4, max_y - 2, max_x + 4, max_y + margin_px),
        ("left", min_x - margin_px, min_y - 2, min_x + 2, max_y + 2),
        ("right", max_x - 2, min_y - 2, max_x + margin_px, max_y + 2),
        ("core", min_x, min_y, max_x, max_y),
    ]
    parts: list[dict[str, Any]] = [seed]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = REPO / "_tmp_logs" / "hitboxes"
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, (name, x0, y0, x1, y1) in enumerate(bands, start=1):
        roi = _pct_roi_from_px(client, x0, y0, x1, y1)
        print(f"=== refine {name} px=({x0},{y0})-({x1},{y1}) pct={roi} ===", flush=True)
        part = _run_one_strip(
            ip=ip,
            expect_type="Fields",
            expect_id=str(number),
            roi=roi,
            step_px=step_px,
            max_points=200,
            duration_ms=17000,
            out_name=f"refine_{button_id}_{stamp}_{i}_{name}.json",
        )
        (out_dir / f"refine_{button_id}_{stamp}_{i}_{name}.json").write_text(
            json.dumps(part, indent=2) + "\n", encoding="utf-8"
        )
        parts.append(part)
        print(
            f"  {name} hits={part.get('hit_count')} wrong={part.get('wrong_count')} "
            f"probed={part.get('probed')}",
            flush=True,
        )

    merged = _merge_hits(parts)
    return _write_result(
        button_id=button_id,
        number=number,
        merged=merged,
        parts=parts,
        stamp=f"refined_{stamp}",
        out_dir=out_dir,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Pixel-perfect straight-up hitbox mapper")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--number", default="1")
    p.add_argument("--id", default="straightup_red_1")
    p.add_argument("--step", type=int, default=3)
    p.add_argument(
        "--refine",
        metavar="SEED_JSON",
        help="Expand edges around existing hit JSON instead of coarse scan",
    )
    p.add_argument("--seed", metavar="SEED_JSON", help="Merge prior hits into a new map")
    args = p.parse_args(argv)
    if args.refine:
        data = refine_straight_hitbox(
            ip=args.ip,
            number=args.number,
            button_id=args.id,
            seed_path=args.refine,
            step_px=max(2, args.step),
        )
    else:
        data = map_straight_hitbox(
            ip=args.ip,
            number=args.number,
            button_id=args.id,
            step_px=args.step,
            seed_path=args.seed,
        )
    return 0 if int(data.get("hit_count") or 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
