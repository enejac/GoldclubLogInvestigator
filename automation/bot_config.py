"""
Tunable timing / behaviour for the roulette UI click bot.

Default on-disk file: ``automation/bot_config.json`` (bundled with the exe).
UI and CLI can switch profiles or edit values; saves go to a writable path
(repo file when developing, AppData when frozen).
"""

from __future__ import annotations

import json
import sys
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

PROFILE_EMULATION = "emulation"
PROFILE_SAFE = "safe"
PROFILE_FAST = "fast"
PROFILE_CUSTOM = "custom"
DEFAULT_PROFILE = PROFILE_EMULATION

_active_cfg: ContextVar["BotConfig | None"] = ContextVar("bot_cfg", default=None)


@dataclass(slots=True)
class BotConfig:
    """Click timing + sweep behaviour for ``run_random_bot``."""

    # Profile label (informational; file may store several).
    profile: str = PROFILE_EMULATION

    # Batching — larger = fewer InputAgent round-trips (faster).
    batch_size: int = 24

    # Per-click press / inter-click gap (ms).
    click_ms: int = 28
    gap_ms: int = 18
    start_click_ms: int = 40
    start_gap_ms: int = 45
    start_double_tap: bool = False
    start_double_gap_ms: int = 40

    # Overlay / skin settle after open/close/switch (ms).
    overlay_open_ms: int = 400
    overlay_close_ms: int = 280
    layout_switch_ms: int = 700

    # Focus + InputAgent key delay (ms).
    focus_ms: int = 40
    focus_short_ms: int = 25
    default_key_delay_ms: int = 10

    # Wait after "Bets are open" before clicking (seconds).
    open_ui_settle_sec: float = 0.20
    open_ui_settle_start_sec: float = 0.35
    wait_betting_timeout_sec: float = 120.0

    # Sweep behaviour.
    wait_betting: bool = True
    include_unverified: bool = True
    shuffle_layouts: bool = True
    min_credits: int = 100
    cancel_cloth_before_bets: bool = True

    # While betting is closed / waiting for the next open: randomize UI clicks
    # that do not require an open window (history, help, language, stats, …).
    # Off by default — closed-phase MENU/AYUDA spam can strand the bot in panels.
    closed_phase_enabled: bool = False
    closed_batch_size: int = 16
    closed_clicks_max_per_wait: int = 48

    # Optional human-ish jitter on gaps (0 = pure emulation cadence).
    gap_jitter_ms: int = 0

    def clamp(self) -> BotConfig:
        """Return a copy with sane bounds (mutates self and returns it)."""
        self.batch_size = max(1, min(48, int(self.batch_size)))
        self.closed_batch_size = max(1, min(48, int(self.closed_batch_size)))
        self.closed_clicks_max_per_wait = max(
            0, min(500, int(self.closed_clicks_max_per_wait))
        )
        for name in (
            "click_ms",
            "gap_ms",
            "start_click_ms",
            "start_gap_ms",
            "start_double_gap_ms",
            "overlay_open_ms",
            "overlay_close_ms",
            "layout_switch_ms",
            "focus_ms",
            "focus_short_ms",
            "default_key_delay_ms",
            "gap_jitter_ms",
        ):
            setattr(self, name, max(0, min(10_000, int(getattr(self, name)))))
        self.open_ui_settle_sec = max(0.0, min(10.0, float(self.open_ui_settle_sec)))
        self.open_ui_settle_start_sec = max(
            0.0, min(10.0, float(self.open_ui_settle_start_sec))
        )
        self.wait_betting_timeout_sec = max(
            5.0, min(600.0, float(self.wait_betting_timeout_sec))
        )
        self.min_credits = max(0, min(1_000_000, int(self.min_credits)))
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, *, profile: str | None = None) -> BotConfig:
        if not data:
            cfg = cls(profile=profile or DEFAULT_PROFILE)
            return cfg.clamp()
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        if profile is not None:
            kwargs["profile"] = profile
        elif "profile" not in kwargs:
            kwargs["profile"] = DEFAULT_PROFILE
        return cls(**kwargs).clamp()


# Built-in presets (also written into the default JSON).
PRESET_EMULATION = BotConfig(
    profile=PROFILE_EMULATION,
    batch_size=12,
    click_ms=45,
    gap_ms=35,
    start_click_ms=55,
    start_gap_ms=70,
    start_double_tap=False,
    start_double_gap_ms=55,
    overlay_open_ms=550,
    overlay_close_ms=400,
    layout_switch_ms=900,
    focus_ms=60,
    focus_short_ms=40,
    default_key_delay_ms=15,
    open_ui_settle_sec=0.35,
    open_ui_settle_start_sec=0.55,
    closed_phase_enabled=False,
    closed_batch_size=12,
    closed_clicks_max_per_wait=0,
)

PRESET_FAST = BotConfig(
    profile=PROFILE_FAST,
    batch_size=24,
    click_ms=28,
    gap_ms=18,
    start_click_ms=40,
    start_gap_ms=45,
    start_double_tap=False,
    start_double_gap_ms=40,
    overlay_open_ms=400,
    overlay_close_ms=280,
    layout_switch_ms=700,
    focus_ms=40,
    focus_short_ms=25,
    default_key_delay_ms=10,
    open_ui_settle_sec=0.20,
    open_ui_settle_start_sec=0.35,
    closed_phase_enabled=False,
    closed_batch_size=16,
    closed_clicks_max_per_wait=0,
)

PRESET_SAFE = BotConfig(
    profile=PROFILE_SAFE,
    batch_size=6,
    click_ms=95,
    gap_ms=120,
    start_click_ms=130,
    start_gap_ms=220,
    start_double_tap=False,
    start_double_gap_ms=180,
    overlay_open_ms=1600,
    overlay_close_ms=1200,
    layout_switch_ms=2200,
    focus_ms=180,
    focus_short_ms=100,
    default_key_delay_ms=35,
    open_ui_settle_sec=0.85,
    open_ui_settle_start_sec=1.25,
    closed_phase_enabled=False,
    closed_batch_size=6,
    closed_clicks_max_per_wait=0,
)

# Starting point for user edits (same as fast until changed).
PRESET_CUSTOM = BotConfig(**{**PRESET_FAST.to_dict(), "profile": PROFILE_CUSTOM})

BUILTIN_PRESETS: dict[str, BotConfig] = {
    PROFILE_EMULATION: PRESET_EMULATION,
    PROFILE_FAST: PRESET_FAST,
    PROFILE_SAFE: PRESET_SAFE,
    PROFILE_CUSTOM: PRESET_CUSTOM,
}


def package_bot_config_path() -> Path:
    """Bundled / repo default JSON next to this module (or under _MEIPASS)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "automation" / "bot_config.json"
    return Path(__file__).resolve().parent / "bot_config.json"


def user_bot_config_path() -> Path:
    """Writable override (AppData when frozen, repo file when developing)."""
    if getattr(sys, "frozen", False):
        base = Path.home() / "AppData" / "Roaming" / "Goldclub" / "LogInvestigator"
        base.mkdir(parents=True, exist_ok=True)
        return base / "bot_config.json"
    return Path(__file__).resolve().parent / "bot_config.json"


def resolve_bot_config_path(explicit: Path | str | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    user = user_bot_config_path()
    if user.is_file():
        return user
    pkg = package_bot_config_path()
    if pkg.is_file():
        return pkg
    return user


def _default_file_payload() -> dict[str, Any]:
    return {
        "active_profile": DEFAULT_PROFILE,
        "notes": (
            "fast = dense open-window batches + closed-phase UI clicks (default). "
            "emulation = prior snappy cadence. "
            "safe = original slow settles. "
            "custom = edit in UI or by hand."
        ),
        "profiles": {
            name: cfg.to_dict() for name, cfg in BUILTIN_PRESETS.items()
        },
    }


def ensure_bot_config_file(path: Path | None = None) -> Path:
    """Create the JSON with built-in profiles if missing; return path used."""
    target = path or user_bot_config_path()
    if not target.is_file():
        # Prefer copying the bundled file when present.
        pkg = package_bot_config_path()
        if pkg.is_file() and pkg.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(pkg.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(_default_file_payload(), indent=2) + "\n",
                encoding="utf-8",
            )
    return target


def load_bot_config_file(path: Path | str | None = None) -> dict[str, Any]:
    p = resolve_bot_config_path(path)
    if not p.is_file():
        ensure_bot_config_file(p if path else None)
        p = resolve_bot_config_path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_file_payload()
    if not isinstance(raw, dict):
        return _default_file_payload()
    return raw


def list_profiles(path: Path | str | None = None) -> list[str]:
    data = load_bot_config_file(path)
    profiles = data.get("profiles") or {}
    names = [str(k) for k in profiles.keys()]
    for builtin in BUILTIN_PRESETS:
        if builtin not in names:
            names.append(builtin)
    # Prefer known order first.
    ordered = [n for n in (PROFILE_EMULATION, PROFILE_SAFE, PROFILE_CUSTOM) if n in names]
    ordered.extend(n for n in names if n not in ordered)
    return ordered


def load_bot_config(
    profile: str | None = None,
    path: Path | str | None = None,
) -> BotConfig:
    """Load active (or named) profile from JSON, falling back to built-ins."""
    data = load_bot_config_file(path)
    name = (profile or data.get("active_profile") or DEFAULT_PROFILE).strip()
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    blob = profiles.get(name) if isinstance(profiles, dict) else None
    if isinstance(blob, dict):
        return BotConfig.from_dict(blob, profile=name)
    if name in BUILTIN_PRESETS:
        return deepcopy(BUILTIN_PRESETS[name])
    return deepcopy(PRESET_EMULATION)


def save_bot_config(
    cfg: BotConfig,
    *,
    path: Path | str | None = None,
    make_active: bool = True,
) -> Path:
    """Write ``cfg`` into its profile slot and optionally set active_profile."""
    target = Path(path) if path else user_bot_config_path()
    data = load_bot_config_file(target if target.is_file() else None)
    if not isinstance(data.get("profiles"), dict):
        data = _default_file_payload()
    profiles: dict[str, Any] = dict(data["profiles"])
    cfg = cfg.clamp()
    name = (cfg.profile or PROFILE_CUSTOM).strip() or PROFILE_CUSTOM
    cfg.profile = name
    profiles[name] = cfg.to_dict()
    data["profiles"] = profiles
    if make_active:
        data["active_profile"] = name
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2) + "\n"
    # UTF-8 without BOM
    target.write_text(text, encoding="utf-8")
    return target


def set_active_profile(name: str, path: Path | str | None = None) -> Path:
    target = Path(path) if path else user_bot_config_path()
    data = load_bot_config_file(target if target.is_file() else None)
    data["active_profile"] = name
    if "profiles" not in data or not isinstance(data["profiles"], dict):
        data["profiles"] = {k: v.to_dict() for k, v in BUILTIN_PRESETS.items()}
    if name not in data["profiles"] and name in BUILTIN_PRESETS:
        data["profiles"][name] = BUILTIN_PRESETS[name].to_dict()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return target


def push_bot_config(cfg: BotConfig):
    """Install cfg for the current context (nested helpers read via ``active_bot_config``)."""
    return _active_cfg.set(cfg.clamp())


def reset_bot_config(token) -> None:
    _active_cfg.reset(token)


def active_bot_config() -> BotConfig:
    cur = _active_cfg.get()
    if cur is not None:
        return cur
    return deepcopy(PRESET_EMULATION)


# Recommended future knobs (documented for UI / operators; not all wired yet).
RECOMMENDED_OPTIONS: tuple[tuple[str, str], ...] = (
    ("aft_auto_topup", "Auto AFT when credits < min_credits (bug-hunt / endurance)."),
    ("aft_amount", "Credits / cents to send on auto top-up."),
    ("cycle_pause_ms", "Pause between bug-hunt cycles."),
    ("stop_on_first_fault", "Exit hunt after packing the first godot1 fault."),
    ("pack_on_fault", "Write BUG-TEST/BUG-DEV repro packs on critical log hits."),
    ("never_click_extra", "Extra button ids to skip beyond COBRAR/LLAMAR/locks."),
    ("max_clicks_per_cycle", "Hard cap per hunt cycle (0 = catalog size)."),
    ("client_ids", "Multi-seat rotation list (player0, player1, …)."),
    ("prefer_layouts", "Layout visit order / weight."),
    ("retry_failed_batches", "Re-queue failed batch targets once."),
)
